"""Public, unauthenticated share pages (P41 B).

GET /public/r/{slug} returns a read-only recipe view: title, description,
ingredients with their deal-price story, instructions, computed nutrition,
and the sharer's FIRST NAME at most. It never includes pantry data — the
``in_pantry`` flag and any cost derived from ownership are stripped.
GET /public/r/{slug}/og.png renders the link-preview card server-side.
"""

import io
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.recipe import Recipe
from app.models.user import User
from app.services import events

router = APIRouter(prefix="/public", tags=["public"])


async def _shared_recipe(db: AsyncSession, slug: str) -> tuple[Recipe, User | None]:
    recipe = await db.scalar(select(Recipe).where(Recipe.share_slug == slug))
    if recipe is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found."
        )
    owner = await db.get(User, recipe.user_id)
    return recipe, owner


def _first_name(owner: User | None) -> str | None:
    if owner and owner.name and owner.name.strip():
        return owner.name.strip().split()[0]
    return None


def _public_ingredients(recipe: Recipe) -> list[dict]:
    """Ingredient lines with the deal-price story, pantry data stripped."""
    out = []
    for raw in recipe.ingredients_json or []:
        if not isinstance(raw, dict):
            continue
        out.append(
            {
                "name": raw.get("name") or raw.get("generic_name"),
                "quantity": raw.get("quantity"),
                "unit": raw.get("unit"),
                "on_sale": bool(raw.get("on_sale")),
                "sale_price": raw.get("sale_price"),
                "sale_store": raw.get("sale_store"),
            }
        )
    return out


@router.get("/r/{slug}")
async def shared_recipe(
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    recipe, owner = await _shared_recipe(db, slug)
    # share_visited is attributed to the OWNER (visits to their link) since
    # the visitor is anonymous.
    events.log(db, recipe.user_id, "share_visited", slug=slug)
    await db.flush()

    anchor = recipe.market_anchor_json or {}
    return {
        "slug": slug,
        "title": recipe.title,
        "description": recipe.description,
        "first_name": _first_name(owner),
        "store_name": recipe.generated_store_name,
        "difficulty": recipe.difficulty,
        "total_time_min": recipe.total_time_min,
        "servings": recipe.servings,
        "ingredients": _public_ingredients(recipe),
        "instructions": recipe.instructions_json or [],
        "nutrition_per_serving": recipe.nutrition_json,
        "market_anchor": (
            {
                "name": anchor.get("name") or anchor.get("product_name"),
                "sale_price": anchor.get("sale_price"),
                "price_unit": anchor.get("price_unit"),
                "store": anchor.get("store"),
            }
            if recipe.is_market_pick and anchor
            else None
        ),
    }


def _fmt_price(v) -> str:
    try:
        return f"${Decimal(str(v)):.2f}"
    except Exception:  # noqa: BLE001
        return ""


@router.get("/r/{slug}/og.png")
async def shared_recipe_og(
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """1200×630 link-preview card, drawn server-side with Pillow."""
    from PIL import Image, ImageDraw, ImageFont

    recipe, owner = await _shared_recipe(db, slug)

    W, H = 1200, 630
    img = Image.new("RGB", (W, H), (16, 92, 62))  # PantryAI brand green
    d = ImageDraw.Draw(img)

    def font(size: int, bold: bool = False):
        for path in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def wrap(text: str, fnt, max_w: int) -> list[str]:
        words, lines, cur = text.split(), [], ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if d.textlength(trial, font=fnt) <= max_w:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines[:3]

    pad = 70
    d.text((pad, 60), "🥬 PantryAI", font=font(40, bold=True), fill=(220, 240, 230))

    y = 170
    for line in wrap(recipe.title, font(72, bold=True), W - 2 * pad):
        d.text((pad, y), line, font=font(72, bold=True), fill=(255, 255, 255))
        y += 88

    # The deal-price story: the market anchor's real flyer price, if any.
    anchor = recipe.market_anchor_json or {}
    story = None
    if recipe.is_market_pick and anchor:
        name = anchor.get("name") or anchor.get("product_name")
        price = _fmt_price(anchor.get("sale_price"))
        if name and price:
            unit = anchor.get("price_unit")
            store = anchor.get("store") or recipe.generated_store_name
            story = f"Built on {name} at {price}{'/' + unit if unit else ''}" + (
                f" — {store}" if store else ""
            )
    elif recipe.generated_store_name:
        story = f"Built from this week's flyer at {recipe.generated_store_name}"
    if story:
        y += 20
        for line in wrap(story, font(40), W - 2 * pad):
            d.text((pad, y), line, font=font(40), fill=(190, 230, 205))
            y += 52

    meta_bits = []
    if recipe.total_time_min:
        meta_bits.append(f"{recipe.total_time_min} min")
    n = recipe.nutrition_json or {}
    if isinstance(n, dict) and n.get("protein_g"):
        meta_bits.append(f"{round(float(n['protein_g']))}g protein")
    who = _first_name(owner)
    if who:
        meta_bits.append(f"shared by {who}")
    if meta_bits:
        d.text(
            (pad, H - 130), "  ·  ".join(meta_bits), font=font(36),
            fill=(220, 240, 230),
        )
    d.text(
        (pad, H - 76),
        "Get dinners from YOUR store's flyer →",
        font=font(36, bold=True),
        fill=(255, 214, 102),
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )
