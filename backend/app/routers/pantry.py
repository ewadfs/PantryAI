"""Pantry endpoints: AI scan, confirm, list, and manual item CRUD."""

from datetime import date, datetime, timedelta, timezone

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.ingredient import IngredientMaster
from app.models.pantry import PantryItem, PantryScan
from app.models.user import User
from app.schemas.pantry import (
    ConfirmRequest,
    ConfirmResponse,
    PantryCategoryGroup,
    PantryItemCreate,
    PantryItemRead,
    PantryItemUpdate,
    PantryListResponse,
    ScanResponse,
)
from app.services import ingredient_matcher, recipe_engine, vision
from app.services.auth import get_current_user

router = APIRouter(prefix="/pantry", tags=["pantry"])

MAX_IMAGES = 6
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB
USE_SOON_WINDOW_DAYS = 2


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _shelf_life_map(db: AsyncSession) -> dict[int, int | None]:
    rows = (
        await db.execute(
            select(IngredientMaster.id, IngredientMaster.shelf_life_days)
        )
    ).all()
    return dict(rows)


def _compute_use_soon(item: PantryItem, today: date) -> bool:
    if item.freshness == "use_soon":
        return True
    if item.estimated_expiry is not None:
        return item.estimated_expiry <= today + timedelta(days=USE_SOON_WINDOW_DAYS)
    return False


@router.post("/scan", response_model=ScanResponse)
async def scan_pantry(
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ScanResponse:
    """Upload 1-6 pantry/fridge photos and get back detected items."""
    if not files or len(files) > MAX_IMAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provide between 1 and {MAX_IMAGES} images.",
        )

    images: list[bytes] = []
    for f in files:
        data = await f.read()
        if not data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Empty image upload.",
            )
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Each image must be at most 10 MB.",
            )
        images.append(data)

    result = await vision.process_pantry_scan(db, current_user.id, images)
    return ScanResponse(**result)


@router.post("/scan/{scan_id}/confirm", response_model=ConfirmResponse)
async def confirm_scan(
    scan_id: int,
    payload: ConfirmRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConfirmResponse:
    """Reconcile the active pantry with the user's confirmed scan items."""
    scan = await db.scalar(
        select(PantryScan).where(
            PantryScan.id == scan_id, PantryScan.user_id == current_user.id
        )
    )
    if scan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found."
        )

    await ingredient_matcher.preload(db)
    shelf_life = await _shelf_life_map(db)
    today = date.today()
    now = _now()

    # Current active items, keyed by normalized name.
    active = (
        (
            await db.execute(
                select(PantryItem).where(
                    PantryItem.user_id == current_user.id,
                    PantryItem.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    by_name: dict[str, PantryItem] = {
        ingredient_matcher._norm(item.name or ""): item for item in active
    }

    confirmed_keys: set[str] = set()
    for ci in payload.confirmed:
        key = ingredient_matcher._norm(ci.name)
        if not key:
            continue
        confirmed_keys.add(key)
        ingredient_id, match_conf = ingredient_matcher.match_ingredient(ci.name)
        expiry = None
        if ingredient_id is not None:
            days = shelf_life.get(ingredient_id)
            if days:
                expiry = today + timedelta(days=days)

        existing = by_name.get(key)
        if existing is not None:
            existing.name = ci.name
            existing.quantity_estimate = ci.quantity_estimate
            existing.unit = ci.unit
            existing.category = ci.category
            existing.is_staple = ci.is_staple
            existing.ingredient_id = ingredient_id
            existing.confidence = match_conf
            existing.estimated_expiry = expiry
            existing.last_confirmed_at = now
            existing.is_active = True
            existing.consumed_at = None
        else:
            db.add(
                PantryItem(
                    user_id=current_user.id,
                    ingredient_id=ingredient_id,
                    name=ci.name,
                    quantity_estimate=ci.quantity_estimate,
                    unit=ci.unit,
                    category=ci.category,
                    is_staple=ci.is_staple,
                    source="scan",
                    scan_id=scan_id,
                    confidence=match_conf,
                    estimated_expiry=expiry,
                    last_confirmed_at=now,
                )
            )

    # Deactivate items. In "replace" mode, active non-staples missing from the
    # scan are dropped (full-kitchen reconcile). In "merge" mode, only the
    # explicitly-removed items are dropped — absent items are left untouched
    # (so a fridge-only scan never wipes the dry-goods shelf).
    removed_keys = {
        ingredient_matcher._norm(n) for n in payload.removed if n.strip()
    }
    removed_count = 0
    for key, item in by_name.items():
        if payload.mode == "replace":
            drop = key in removed_keys or (
                key not in confirmed_keys and not item.is_staple
            )
        else:  # merge
            drop = key in removed_keys
        if drop and item.is_active:
            item.is_active = False
            item.consumed_at = now
            removed_count += 1

    # Log correction feedback onto the scan.
    if payload.corrections:
        response_json = dict(scan.ai_response_json or {})
        feedback = list(response_json.get("_feedback", []))
        feedback.extend(
            {"ai_said": c.ai_said, "user_said": c.user_said}
            for c in payload.corrections
        )
        response_json["_feedback"] = feedback
        scan.ai_response_json = response_json

    scan.items_confirmed = len(confirmed_keys)
    await db.flush()

    total_active = len(
        (
            await db.execute(
                select(PantryItem.id).where(
                    PantryItem.user_id == current_user.id,
                    PantryItem.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )

    # Warm the Recipes tab: pre-generate a batch from the freshly-saved pantry.
    background_tasks.add_task(recipe_engine.warm_generate, current_user.id)

    return ConfirmResponse(
        scan_id=scan_id,
        confirmed=len(confirmed_keys),
        removed=removed_count,
        active_items=total_active,
    )


@router.get("", response_model=PantryListResponse)
async def list_pantry(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PantryListResponse:
    """Active pantry items grouped by category, staples listed last."""
    items = (
        (
            await db.execute(
                select(PantryItem)
                .where(
                    PantryItem.user_id == current_user.id,
                    PantryItem.is_active.is_(True),
                )
                .order_by(PantryItem.category, PantryItem.name)
            )
        )
        .scalars()
        .all()
    )

    today = date.today()
    non_staple: dict[str, list[PantryItemRead]] = {}
    staples: list[PantryItemRead] = []
    for item in items:
        read = PantryItemRead.model_validate(item)
        read.use_soon = _compute_use_soon(item, today)
        if item.is_staple:
            staples.append(read)
        else:
            non_staple.setdefault(item.category or "other", []).append(read)

    groups = [
        PantryCategoryGroup(category=cat, items=non_staple[cat])
        for cat in sorted(non_staple)
    ]
    if staples:
        groups.append(PantryCategoryGroup(category="staples", items=staples))

    return PantryListResponse(count=len(items), categories=groups)


@router.post(
    "/items", response_model=PantryItemRead, status_code=status.HTTP_201_CREATED
)
async def add_item(
    payload: PantryItemCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PantryItem:
    """Manually add a single pantry item."""
    await ingredient_matcher.preload(db)
    ingredient_id, match_conf = ingredient_matcher.match_ingredient(payload.name)
    expiry = None
    if ingredient_id is not None:
        days = (await _shelf_life_map(db)).get(ingredient_id)
        if days:
            expiry = date.today() + timedelta(days=days)

    item = PantryItem(
        user_id=current_user.id,
        ingredient_id=ingredient_id,
        name=payload.name,
        quantity_estimate=payload.quantity_estimate,
        unit=payload.unit,
        category=payload.category,
        source="manual",
        confidence=match_conf,
        estimated_expiry=expiry,
        last_confirmed_at=_now(),
    )
    db.add(item)
    await db.flush()
    await db.refresh(item)
    return item


@router.patch("/items/{item_id}", response_model=PantryItemRead)
async def update_item(
    item_id: int,
    payload: PantryItemUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PantryItem:
    """Update quantity, freshness, expiry, or staple flag on an item."""
    item = await db.scalar(
        select(PantryItem).where(
            PantryItem.id == item_id,
            PantryItem.user_id == current_user.id,
            PantryItem.is_active.is_(True),
        )
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pantry item not found."
        )

    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(item, field, value)
    await db.flush()
    await db.refresh(item)
    return item


@router.delete("/items/{item_id}", status_code=status.HTTP_200_OK)
async def delete_item(
    item_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int | str]:
    """Soft-delete a pantry item (mark consumed / inactive)."""
    item = await db.scalar(
        select(PantryItem).where(
            PantryItem.id == item_id,
            PantryItem.user_id == current_user.id,
            PantryItem.is_active.is_(True),
        )
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pantry item not found."
        )
    item.is_active = False
    item.consumed_at = _now()
    await db.flush()
    return {"status": "deleted", "id": item_id}
