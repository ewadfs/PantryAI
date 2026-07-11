"""Vision service — turn pantry/fridge photos into structured pantry items.

:class:`PantryScanner` calls Claude Vision (``settings.vision_model``) with each
image and parses a strict JSON payload of detected items. :func:`process_pantry_scan`
orchestrates the full flow: upload originals to R2, scan each photo concurrently,
merge/dedupe across photos, fuzzy-match ingredients, estimate expiries, and
persist a ``pantry_scans`` row.
"""

import asyncio
import base64
import json
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from anthropic import AsyncAnthropic
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.deal import CircularFetch, DealCache
from app.models.ingredient import IngredientMaster
from app.models.pantry import PantryScan
from app.models.store import SupportedChain
from app.services import deal_fetcher, ingredient_matcher, storage

_MAX_CONCURRENT_SCANS = 3

_PROMPT = """You are a kitchen inventory assistant. Analyze this photo of a \
refrigerator, freezer, or pantry and identify the food items you can see.

For each distinct item you can identify, report:
- name: the common food name (e.g. "whole milk", "roma tomatoes", "cheddar cheese")
- quantity_estimate: your best guess at the amount visible (e.g. "1 gallon", \
"~6", "half a bag", "2 containers")
- unit: the unit for the quantity (e.g. "gallon", "count", "bag", "container", \
"oz") or null if unclear
- category: one of produce, dairy, meat, seafood, frozen, bakery, pantry, \
beverages, condiments, snacks, other
- freshness: one of "fresh", "good", "use_soon" based on visual cues (wilting, \
browning, near-empty) — default "good" if you cannot tell
- confidence: a number from 0.0 to 1.0 for how sure you are about this item

Rules:
- Only report items you can actually see. Do not invent items.
- If you are unsure what an item is, put a short description in the "uncertain" \
list instead of guessing in "items".
- Group obviously identical items into one entry with a combined quantity.

Return ONLY a JSON object (no markdown, no prose) with this exact shape:
{
  "items": [
    {"name": "...", "quantity_estimate": "...", "unit": "...", \
"category": "...", "freshness": "...", "confidence": 0.0}
  ],
  "uncertain": ["short description of anything you couldn't identify"],
  "photo_quality": "good | fair | poor",
  "photo_zone": "fridge | freezer | pantry | counter | unknown"
}"""


def _media_type(image_bytes: bytes) -> str:
    """Sniff the image media type from magic bytes; default to JPEG."""
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of a model response, tolerating markdown fences."""
    cleaned = text.strip()
    # Strip ```json ... ``` or ``` ... ``` fences.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fall back to the outermost {...} span.
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


class PantryScanner:
    """Wraps Claude Vision calls for a single scan session."""

    def __init__(self) -> None:
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def scan_pantry_image(self, image_bytes: bytes) -> dict:
        """Send one image to Claude and return the parsed JSON payload.

        Retries once on an unparseable response before giving up with an empty
        result.
        """
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        media_type = _media_type(image_bytes)

        last_exc: Exception | None = None
        for _ in range(2):
            message = await self._client.messages.create(
                model=settings.vision_model,
                max_tokens=2000,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64,
                                },
                            },
                            {"type": "text", "text": _PROMPT},
                        ],
                    }
                ],
            )
            text = "".join(
                block.text for block in message.content if block.type == "text"
            )
            try:
                data = _extract_json(text)
                data.setdefault("items", [])
                data.setdefault("uncertain", [])
                return data
            except (json.JSONDecodeError, ValueError) as exc:
                last_exc = exc
                continue

        # Both attempts failed to parse — degrade gracefully.
        return {
            "items": [],
            "uncertain": [],
            "photo_quality": "poor",
            "photo_zone": "unknown",
            "_parse_error": str(last_exc),
        }


def _quantity_value(quantity_estimate: str | None) -> float:
    """Extract a leading numeric magnitude from a quantity string, else 0."""
    if not quantity_estimate:
        return 0.0
    m = re.search(r"\d+(?:\.\d+)?", quantity_estimate)
    return float(m.group()) if m else 0.0


def _merge_items(per_photo: list[dict]) -> list[dict]:
    """Merge item lists across photos, deduped by normalized name.

    On a duplicate name we keep the entry with the larger parsed quantity (and,
    failing that, the higher confidence), and carry the max confidence forward.
    """
    merged: dict[str, dict] = {}
    for payload in per_photo:
        for raw in payload.get("items", []):
            name = (raw.get("name") or "").strip()
            if not name:
                continue
            key = ingredient_matcher._norm(name)
            if not key:
                continue
            item = {
                "name": name,
                "quantity_estimate": raw.get("quantity_estimate"),
                "unit": raw.get("unit"),
                "category": raw.get("category"),
                "freshness": raw.get("freshness") or "good",
                "confidence": float(raw.get("confidence") or 0.0),
            }
            existing = merged.get(key)
            if existing is None:
                merged[key] = item
                continue
            # Keep the "bigger" observation; carry max confidence.
            cur_q = _quantity_value(existing["quantity_estimate"])
            new_q = _quantity_value(item["quantity_estimate"])
            take_new = new_q > cur_q or (
                new_q == cur_q and item["confidence"] > existing["confidence"]
            )
            winner = item if take_new else existing
            winner["confidence"] = max(existing["confidence"], item["confidence"])
            merged[key] = winner
    return list(merged.values())


async def process_pantry_scan(
    db: AsyncSession, user_id: int, images: list[bytes]
) -> dict:
    """Run the full scan pipeline for a user's uploaded images.

    Returns a payload with the persisted ``scan_id``, the merged & matched
    ``items``, the union of ``uncertain`` descriptions, and ``photo_count``.
    """
    # 1. Create the scan row first so we have an id for the R2 key path.
    scan = PantryScan(user_id=user_id, items_detected=0, items_confirmed=0)
    db.add(scan)
    await db.flush()  # assigns scan.id

    # 2. Upload originals to R2 under pantry/{user_id}/{scan_id}/{n}.jpg.
    keys = [
        f"pantry/{user_id}/{scan.id}/{n}.jpg" for n in range(len(images))
    ]
    await asyncio.gather(
        *(storage.upload_image(img, key) for img, key in zip(images, keys))
    )

    # 3. Scan each image, capped at _MAX_CONCURRENT_SCANS in flight.
    scanner = PantryScanner()
    sem = asyncio.Semaphore(_MAX_CONCURRENT_SCANS)

    async def _scan(img: bytes) -> dict:
        async with sem:
            return await scanner.scan_pantry_image(img)

    per_photo = await asyncio.gather(*(_scan(img) for img in images))

    # 4. Merge across photos.
    items = _merge_items(per_photo)
    uncertain: list[str] = []
    for payload in per_photo:
        uncertain.extend(payload.get("uncertain", []))

    # 5. Fuzzy-match ingredients and (6) estimate expiries.
    await ingredient_matcher.preload(db)
    shelf_life = dict(
        (
            await db.execute(
                select(IngredientMaster.id, IngredientMaster.shelf_life_days)
            )
        ).all()
    )
    today = date.today()
    for item in items:
        ingredient_id, match_conf = ingredient_matcher.match_ingredient(item["name"])
        item["ingredient_id"] = ingredient_id
        item["match_confidence"] = match_conf
        expiry = None
        if ingredient_id is not None:
            days = shelf_life.get(ingredient_id)
            if days:
                expiry = (today + timedelta(days=days)).isoformat()
        item["estimated_expiry"] = expiry

    # 7. Persist scan metadata.
    scan.image_keys = keys
    scan.items_detected = len(items)
    scan.ai_response_json = {
        "photos": per_photo,
        "merged_items": items,
        "uncertain": uncertain,
    }
    await db.flush()

    return {
        "scan_id": scan.id,
        "items": items,
        "uncertain": uncertain,
        "photo_count": len(images),
    }


# ---------------------------------------------------------------------------
# Circular deal extraction
# ---------------------------------------------------------------------------

_MAX_CONCURRENT_PAGES = 3

_DEAL_PROMPT = """Analyze this grocery circular page from {chain_name}. Extract \
every deal/sale item.

For each: product_name; brand (null if store brand/unspecified); sale_price \
(decimal — for "2 for $5" use 2.50); price_unit ("per lb"|"each"|"16 oz"|"per \
pkg"|...); regular_price (null if not printed); deal_type (sale|bogo|buy_x_get_y|\
digital_coupon|percent_off); deal_details (terms for complex deals, else null); \
category (produce|meat|seafood|dairy|frozen|bakery|deli|snacks|beverages|pantry|\
household|other); purchase_limit (null if none); confidence 0.0-1.0.

Rules: prices precise to the cent; BOGO → sale_price = single-item price; skip \
non-food unless household essentials; lower confidence when text is small or \
blurry.

Return ONLY valid JSON: {{"deals":[...]}}"""


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _trim(value: Any, length: int) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s[:length] if s else None


class CircularExtractor:
    """Runs Claude Vision over circular pages and caches the extracted deals."""

    def __init__(self) -> None:
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def extract_deals_from_page(
        self, image_bytes: bytes, chain_name: str
    ) -> list[dict]:
        """Extract the list of deal dicts from one circular page image."""
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        media_type = _media_type(image_bytes)
        prompt = _DEAL_PROMPT.format(chain_name=chain_name)

        for _ in range(2):
            message = await self._client.messages.create(
                model=settings.vision_model,
                # Dense circular pages can hold 30+ items; at 4000 tokens the
                # JSON was truncated mid-object (stop_reason=max_tokens) and
                # failed to parse, dropping the whole page. 8000 fits a full page.
                max_tokens=8000,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
            text = "".join(
                block.text for block in message.content if block.type == "text"
            )
            try:
                data = _extract_json(text)
                deals = data.get("deals", [])
                return deals if isinstance(deals, list) else []
            except (json.JSONDecodeError, ValueError):
                continue
        return []

    def _build_deal(
        self,
        raw: dict,
        *,
        chain_id: int,
        fetch_id: int,
        valid_from: date | None,
        valid_to: date | None,
        page_number: int,
    ) -> DealCache | None:
        """Validate + map one extracted deal to a ``DealCache`` row, or drop it."""
        name = _trim(raw.get("product_name"), 300)
        if not name:
            return None

        sale = _to_decimal(raw.get("sale_price"))
        if sale is None or sale <= 0:
            return None

        confidence = raw.get("confidence")
        if confidence is not None:
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                confidence = None
            else:
                if not 0.0 <= confidence <= 1.0:
                    return None  # confidence out of range -> drop

        regular = _to_decimal(raw.get("regular_price"))
        savings_pct = None
        if regular is not None and regular > 0:
            savings_pct = ((regular - sale) / regular * 100).quantize(
                Decimal("0.01")
            )

        ingredient_id, match_conf = ingredient_matcher.match_ingredient(name)

        return DealCache(
            chain_id=chain_id,
            fetch_id=fetch_id,
            product_name=name,
            brand=_trim(raw.get("brand"), 200),
            sale_price=sale,
            price_unit=_trim(raw.get("price_unit"), 50),
            regular_price=regular,
            savings_pct=savings_pct,
            deal_type=_trim(raw.get("deal_type"), 30),
            deal_details=_trim(raw.get("deal_details"), 10_000),
            category=_trim(raw.get("category"), 50),
            purchase_limit=_trim(raw.get("purchase_limit"), 50),
            confidence=(
                Decimal(str(round(confidence, 2)))
                if confidence is not None
                else None
            ),
            matched_ingredient_id=ingredient_id,
            match_confidence=(
                Decimal(str(match_conf)) if ingredient_id is not None else None
            ),
            valid_from=valid_from,
            valid_to=valid_to,
            page_number=page_number,
        )

    async def process_circular(self, db: AsyncSession, fetch_id: int) -> dict:
        """Extract deals from every page of a fetch and cache them.

        Stale ``deal_cache`` rows for this chain (``valid_to < today``) are purged
        first. Returns ``{pages, deals, matched, regular_price}`` counts.
        """
        fetch = await db.get(CircularFetch, fetch_id)
        if fetch is None:
            raise ValueError(f"CircularFetch {fetch_id} not found.")
        chain = await db.get(SupportedChain, fetch.chain_id)
        chain_name = chain.chain_name if chain else "this store"

        today = date.today()
        await db.execute(
            delete(DealCache).where(
                DealCache.chain_id == fetch.chain_id,
                DealCache.valid_to < today,
            )
        )
        await ingredient_matcher.preload(db)
        # Release the DB connection before the multi-minute Vision phase — holding
        # an open transaction across it lets managed-host proxies drop the idle
        # connection out from under the later bulk insert. expire_on_commit=False
        # keeps ``fetch``/``chain`` usable afterward.
        await db.commit()

        keys = fetch.image_keys or []
        sem = asyncio.Semaphore(_MAX_CONCURRENT_PAGES)

        async def _page(page_number: int, key: str) -> tuple[int, list[dict]]:
            async with sem:
                img = await storage.get_image_bytes(key)
                deals = await self.extract_deals_from_page(img, chain_name)
            return page_number, deals

        results = await asyncio.gather(
            *(_page(n, key) for n, key in enumerate(keys, start=1))
        )

        rows: list[DealCache] = []
        matched = 0
        with_regular = 0
        for page_number, deals in results:
            for raw in deals:
                row = self._build_deal(
                    raw,
                    chain_id=fetch.chain_id,
                    fetch_id=fetch.id,
                    valid_from=fetch.valid_from,
                    valid_to=fetch.valid_to,
                    page_number=page_number,
                )
                if row is None:
                    continue
                if row.matched_ingredient_id is not None:
                    matched += 1
                if row.regular_price is not None:
                    with_regular += 1
                rows.append(row)

        db.add_all(rows)
        await db.flush()
        return {
            "pages": len(keys),
            "deals": len(rows),
            "matched": matched,
            "regular_price": with_regular,
        }

    async def run_pipeline(
        self, db: AsyncSession, chain_slugs: list[str] | None = None
    ) -> list[dict]:
        """Fetch + process each chain's circular; one failure can't stop others.

        Skips chains that already have a ``success`` fetch still valid today.
        Returns a per-chain summary list.
        """
        fetcher = deal_fetcher.CircularFetcher()
        today = date.today()

        query = select(SupportedChain).where(
            SupportedChain.is_active.is_(True),
            SupportedChain.has_weekly_circular.is_(True),
        )
        if chain_slugs:
            query = query.where(SupportedChain.chain_slug.in_(chain_slugs))
        chains = (
            (await db.execute(query.order_by(SupportedChain.id))).scalars().all()
        )

        summary: list[dict] = []
        for chain in chains:
            # Capture identity up front: after a rollback the ORM object is
            # expired, and lazy-loading it in the error path would attempt DB IO
            # outside the async greenlet.
            slug = chain.chain_slug
            chain_id = chain.id
            try:
                existing = await db.scalar(
                    select(CircularFetch)
                    .where(
                        CircularFetch.chain_id == chain_id,
                        CircularFetch.status == "success",
                        CircularFetch.valid_to >= today,
                    )
                    .order_by(CircularFetch.fetched_at.desc())
                )
                if existing is not None:
                    deals = await db.scalar(
                        select(func.count())
                        .select_from(DealCache)
                        .where(
                            DealCache.chain_id == chain_id,
                            DealCache.valid_to >= today,
                        )
                    )
                    matched = await db.scalar(
                        select(func.count())
                        .select_from(DealCache)
                        .where(
                            DealCache.chain_id == chain_id,
                            DealCache.valid_to >= today,
                            DealCache.matched_ingredient_id.isnot(None),
                        )
                    )
                    summary.append(
                        {
                            "chain": slug,
                            "status": "skipped",
                            "pages": existing.page_count or 0,
                            "deals": deals or 0,
                            "matched": matched or 0,
                        }
                    )
                    continue

                fetch = await fetcher.fetch_circular(db, chain)
                # Persist the fetch (and notes) before the expensive extract, so a
                # later failure doesn't discard the downloaded pages.
                await db.commit()

                if fetch.status == "failed":
                    summary.append(
                        {
                            "chain": slug,
                            "status": "failed",
                            "pages": 0,
                            "deals": 0,
                            "matched": 0,
                            "error": fetch.error_message,
                        }
                    )
                    continue

                result = await self.process_circular(db, fetch.id)
                await db.commit()
                summary.append(
                    {
                        "chain": slug,
                        "status": fetch.status,
                        "pages": result["pages"],
                        "deals": result["deals"],
                        "matched": result["matched"],
                        "regular_price": result["regular_price"],
                    }
                )
            except Exception as exc:  # noqa: BLE001 - isolate per-chain failures
                await db.rollback()
                summary.append(
                    {
                        "chain": slug,
                        "status": "error",
                        "pages": 0,
                        "deals": 0,
                        "matched": 0,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        return summary
