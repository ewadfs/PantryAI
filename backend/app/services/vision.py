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
import logging
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any

import httpx
from anthropic import AsyncAnthropic
from PIL import Image
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal

from app.config import settings
from app.models.deal import CircularFetch, DealCache
from app.services import ai_metering
from app.models.ingredient import IngredientMaster
from app.models.pantry import PantryScan
from app.models.store import StoreLocation, SupportedChain, UserStore
from app.services import deal_fetcher, ingredient_matcher, regions, storage

logger = logging.getLogger(__name__)

_MAX_CONCURRENT_SCANS = 3
# Message Batches API polling for latency-insensitive circular extraction.
_BATCH_POLL_S = 10
# Ceiling for Batches API polling. Batches usually end in minutes, but under
# load Anthropic can take much longer — abandoning at 15 min threw away paid
# extractions (observed live: two batches "did not finish in 900s" and their
# combos served zero deals). An hour keeps the background task cheap while
# outlasting realistic queue delays.
_BATCH_MAX_WAIT_S = 3600

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
- region: [x0, y0, x1, y1] as normalized 0-1 coordinates of the item's bounding \
area in THIS photo (x0,y0 = top-left corner, x1,y1 = bottom-right). Approximate \
is fine; err generous (a slightly larger box) rather than tight.

Rules:
- Only report items you can actually see. Do not invent items.
- Group obviously identical items into one entry with a combined quantity.
- NEVER emit generic placeholder items like "produce in crisper drawer" or \
"meat (cooked, in tray)". Either identify the specific ingredient(s) or route a \
short description to the "uncertain" list.
- Prepared/cooked leftovers and unidentifiable bottles or containers may go in \
"uncertain" IF they are clearly food.
- Single-serve impulse beverages (soda cans, bottled coffee drinks, energy \
drinks) are low recipe value: still include them, but set category="beverages" \
and a low, honest confidence.

UNCERTAIN entries — be sparing and useful. Only emit one when you can offer at \
least one plausible FOOD guess, OR the item is clearly food but unreadable. Do \
NOT emit uncertain entries for opaque packaging, stacked boxes, or containers \
with no visible food evidence — omit those entirely. Non-food household items \
are either confidently identified (e.g. "Ziploc bags", "paper towels" in items) \
or omitted; NEVER put non-food in uncertain. Each uncertain entry has:
- description: a short phrase for what you see (e.g. "clear tub of red sauce")
- guesses: 1-3 plausible specific food names as quick picks (e.g. ["marinara", \
"tomato soup"]); [] only if truly clueless but it's obviously food
- region: [x0, y0, x1, y1] normalized 0-1 box for the item, same rules as above

Return ONLY a JSON object (no markdown, no prose) with this exact shape:
{
  "items": [
    {"name": "...", "quantity_estimate": "...", "unit": "...", \
"category": "...", "freshness": "...", "confidence": 0.0, \
"region": [0.0, 0.0, 0.0, 0.0]}
  ],
  "uncertain": [
    {"description": "...", "guesses": ["..."], "region": [0.0, 0.0, 0.0, 0.0]}
  ],
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
                max_tokens=3000,
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
            ai_metering.record_usage(
                settings.vision_model, message.usage, category="scan"
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


_CROP_PAD = 0.15         # pad the box 15% of its size on each side
_CROP_LONGEST_EDGE = 320
_CROP_JPEG_QUALITY = 70


def _valid_region(region: Any) -> tuple[float, float, float, float] | None:
    """Validate a normalized [x0,y0,x1,y1] box; return it clamped, or None."""
    if not isinstance(region, (list, tuple)) or len(region) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in region)
    except (TypeError, ValueError):
        return None
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    # Reject degenerate / out-of-range boxes.
    if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
        # Allow a little overshoot, then clamp.
        x0, y0 = max(0.0, x0), max(0.0, y0)
        x1, y1 = min(1.0, x1), min(1.0, y1)
        if x1 - x0 < 0.01 or y1 - y0 < 0.01:
            return None
    return (x0, y0, x1, y1)


def _make_crop(image_bytes: bytes, region: tuple[float, float, float, float]) -> bytes:
    """Crop ``region`` from the image (padded ~15%), downscale, JPEG-encode."""
    img = Image.open(BytesIO(image_bytes))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    x0, y0, x1, y1 = region
    bw, bh = (x1 - x0), (y1 - y0)
    px0 = max(0.0, x0 - bw * _CROP_PAD)
    py0 = max(0.0, y0 - bh * _CROP_PAD)
    px1 = min(1.0, x1 + bw * _CROP_PAD)
    py1 = min(1.0, y1 + bh * _CROP_PAD)
    box = (int(px0 * w), int(py0 * h), int(px1 * w), int(py1 * h))
    crop = img.crop(box)
    crop.thumbnail((_CROP_LONGEST_EDGE, _CROP_LONGEST_EDGE))
    buf = BytesIO()
    crop.convert("RGB").save(buf, format="JPEG", quality=_CROP_JPEG_QUALITY)
    return buf.getvalue()


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

    with ai_metering.metering("scan", user_id=user_id) as _cost_events:
        per_photo = await asyncio.gather(*(_scan(img) for img in images))
    await ai_metering.persist_events(db, _cost_events)

    # 4. Merge items; collect uncertain entries tagged with their photo index.
    items = _merge_items(per_photo)
    uncertain_raw: list[dict] = []
    for photo_idx, payload in enumerate(per_photo):
        for u in payload.get("uncertain", []):
            if isinstance(u, str):
                entry = {"description": u, "guesses": [], "region": None}
            elif isinstance(u, dict):
                entry = {
                    "description": (
                        u.get("description") or u.get("name") or "Unidentified item"
                    ),
                    "guesses": [
                        str(g).strip()
                        for g in (u.get("guesses") or [])
                        if str(g).strip()
                    ][:3],
                    "region": u.get("region"),
                }
            else:
                continue
            entry["photo_index"] = photo_idx
            uncertain_raw.append(entry)

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

    # 6.5 Server-side crops for each uncertain entry (+ short-lived GET URLs).
    uncertain = await _crop_uncertain(user_id, scan.id, images, keys, uncertain_raw)

    # 7. Persist scan metadata. Store crop keys (not the expiring URLs).
    scan.image_keys = keys
    scan.items_detected = len(items)
    scan.ai_response_json = {
        "photos": per_photo,
        "merged_items": items,
        "uncertain": [
            {k: v for k, v in u.items() if k != "crop_url"} for u in uncertain
        ],
    }
    await db.flush()

    return {
        "scan_id": scan.id,
        "items": items,
        "uncertain": uncertain,
        "photo_count": len(images),
    }


async def _crop_uncertain(
    user_id: int,
    scan_id: int,
    images: list[bytes],
    keys: list[str],
    uncertain_raw: list[dict],
) -> list[dict]:
    """Crop each uncertain item server-side and return presigned GET URLs.

    Missing/invalid region → presign the full source photo instead and flag
    ``full_photo``. A crop failure never sinks the scan.
    """

    async def _one(n: int, u: dict) -> dict:
        photo_idx = u.get("photo_index", 0)
        if not (0 <= photo_idx < len(images)):
            photo_idx = 0
        region = _valid_region(u.get("region"))
        full_photo = False
        crop_key: str | None = None
        url: str | None = None
        try:
            if region is not None:
                crop_bytes = await asyncio.to_thread(_make_crop, images[photo_idx], region)
                crop_key = f"pantry/{user_id}/{scan_id}/crops/{n}.jpg"
                await storage.upload_image(crop_bytes, crop_key)
                url = await storage.presign_get(crop_key, 600)
            else:
                full_photo = True
                url = await storage.presign_get(keys[photo_idx], 600)
        except Exception:  # noqa: BLE001 - crops are best-effort, fall back to full photo
            full_photo = True
            crop_key = None
            try:
                url = await storage.presign_get(keys[photo_idx], 600)
            except Exception:  # noqa: BLE001
                url = None
        return {
            "description": u["description"],
            "guesses": u.get("guesses", []),
            "crop_url": url,
            "full_photo": full_photo,
            "crop_key": crop_key,
        }

    return list(await asyncio.gather(*(_one(n, u) for n, u in enumerate(uncertain_raw))))


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


class _BatchPending(Exception):
    """A Batches-API extraction outlived the in-process polling ceiling; the
    paid batch is parked on the fetch row for scheduler collection."""

    def __init__(self, batch_id: str):
        self.batch_id = batch_id
        super().__init__(batch_id)


class CircularExtractor:
    """Runs Claude Vision over circular pages and caches the extracted deals."""

    def __init__(self) -> None:
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    def _parse_batch_entry(self, entry) -> tuple[int | None, list[dict]]:
        """One Batches-API result entry -> (page_number, deals)."""
        try:
            page_number = int(str(entry.custom_id).split("-")[-1])
        except (TypeError, ValueError):
            return None, []
        if entry.result.type != "succeeded":
            return page_number, []
        message = entry.result.message
        ai_metering.record_usage(
            settings.vision_model, message.usage, category="circular",
            batch_api=True,
        )
        text = "".join(b.text for b in message.content if b.type == "text")
        try:
            data = _extract_json(text)
            deals = data.get("deals", [])
            return page_number, deals if isinstance(deals, list) else []
        except (json.JSONDecodeError, ValueError):
            return page_number, []

    async def extract_deals_batched(
        self, pages: list[tuple[int, bytes]], chain_name: str
    ) -> dict[int, list[dict]]:
        """Extract deals for every page via the Message Batches API (50% off).

        Circular extraction is latency-insensitive (the cron tolerates
        minutes), so we submit one request per page as a batch, poll until it
        ends, then parse each result. Returns {page_number: deals}. Any page
        that errors or fails to parse degrades to an empty list.
        """
        prompt = _DEAL_PROMPT.format(chain_name=chain_name)
        requests = []
        for page_number, image_bytes in pages:
            b64 = base64.standard_b64encode(image_bytes).decode("ascii")
            requests.append(
                {
                    "custom_id": f"page-{page_number}",
                    "params": {
                        "model": settings.vision_model,
                        "max_tokens": 8000,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": _media_type(image_bytes),
                                            "data": b64,
                                        },
                                    },
                                    {"type": "text", "text": prompt},
                                ],
                            }
                        ],
                    },
                }
            )

        batch = await self._client.messages.batches.create(requests=requests)
        logger.info("Submitted circular batch %s (%d pages)", batch.id, len(requests))

        # Poll until the batch ends (bounded); the cron tolerates minutes.
        waited = 0
        while batch.processing_status != "ended" and waited < _BATCH_MAX_WAIT_S:
            await asyncio.sleep(_BATCH_POLL_S)
            waited += _BATCH_POLL_S
            batch = await self._client.messages.batches.retrieve(batch.id)
        if batch.processing_status != "ended":
            # Never abandon a PAID batch (observed live: 12-page batches
            # queued >90 min) — park it for the scheduler sweep.
            logger.warning(
                "Circular batch %s did not finish in %ds — parking for "
                "scheduler collection", batch.id, waited,
            )
            raise _BatchPending(batch.id)

        out: dict[int, list[dict]] = {}
        async for entry in await self._client.messages.batches.results(batch.id):
            page_number, deals = self._parse_batch_entry(entry)
            if page_number is not None:
                out[page_number] = deals
        logger.info("Circular batch %s parsed %d pages", batch.id, len(out))
        return out

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
            ai_metering.record_usage(
                settings.vision_model, message.usage, category="circular"
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
        region_key: str | None = None,
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

        # Flyer names carry pack/grade/marketing noise — match with the
        # qualifier-stripping normalizer (Prompt 32 3c).
        ingredient_id, match_conf = ingredient_matcher.match_flyer_name(
            name, _trim(raw.get("brand"), 200)
        )

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
            region_key=region_key,
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
        region_key = fetch.region_key

        today = date.today()
        # Purge only this region's stale rows so a fresh fetch for one region
        # never wipes another region's still-valid deals.
        stale = delete(DealCache).where(DealCache.valid_to < today)
        stale = stale.where(
            DealCache.region_key == region_key
            if region_key is not None
            else DealCache.chain_id == fetch.chain_id
        )
        await db.execute(stale)
        await ingredient_matcher.preload(db)
        # Release the DB connection before the multi-minute Vision phase — holding
        # an open transaction across it lets managed-host proxies drop the idle
        # connection out from under the later bulk insert. expire_on_commit=False
        # keeps ``fetch``/``chain`` usable afterward.
        await db.commit()

        keys = fetch.image_keys or []
        # Download page images (concurrency-capped), then extract every page in
        # one Message Batches API job (50% off) — the cron tolerates minutes.
        sem = asyncio.Semaphore(_MAX_CONCURRENT_PAGES)

        async def _load(page_number: int, key: str) -> tuple[int, bytes]:
            async with sem:
                return page_number, await storage.get_image_bytes(key)

        pages = await asyncio.gather(
            *(_load(n, key) for n, key in enumerate(keys, start=1))
        )

        try:
            with ai_metering.metering(
                "circular", circular_fetch_id=fetch.id
            ) as _cost_events:
                deals_by_page = await self.extract_deals_batched(pages, chain_name)
        except _BatchPending as bp:
            fetch.pending_batch_id = bp.batch_id
            await db.commit()
            return {
                "pages": len(keys), "deals": 0, "matched": 0,
                "regular_price": 0, "pending_batch": bp.batch_id,
            }
        results = [(pn, deals_by_page.get(pn, [])) for pn, _img in pages]

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
                    region_key=region_key,
                )
                if row is None:
                    continue
                if row.matched_ingredient_id is not None:
                    matched += 1
                if row.regular_price is not None:
                    with_regular += 1
                rows.append(row)

        if rows:
            # A fresh extraction SUPERSEDES the region's previous rows for
            # this chain — without this, a raced double-activation (observed
            # live: two King Kullen fetches fired within seconds) stacked
            # duplicate deals. Guarded on non-empty rows so a failed/empty
            # extraction never wipes a region to zero.
            supersede = delete(DealCache).where(
                DealCache.chain_id == fetch.chain_id,
                DealCache.fetch_id != fetch.id,
            )
            if region_key is not None:
                supersede = supersede.where(DealCache.region_key == region_key)
            await db.execute(supersede)
        db.add_all(rows)
        await ai_metering.persist_events(db, _cost_events)
        await db.flush()
        return {
            "pages": len(keys),
            "deals": len(rows),
            "matched": matched,
            "regular_price": with_regular,
        }

    async def _combo_has_valid_fetch(
        self, db: AsyncSession, chain_id: int, region_key: str, today: date
    ) -> CircularFetch | None:
        """A fetch only counts as valid if its deals actually landed.

        A successful capture whose EXTRACTION failed (e.g. the Anthropic
        account ran dry mid-batch) used to leave a valid-looking fetch that
        blocked every re-activation until the flyer expired — with zero deals
        served the whole week. Requiring extracted rows makes those poisoned
        fetches invisible, so the next touch simply re-runs the combo.
        """
        return await db.scalar(
            select(CircularFetch)
            .where(
                CircularFetch.chain_id == chain_id,
                CircularFetch.region_key == region_key,
                CircularFetch.status.in_(("success", "partial")),
                CircularFetch.valid_to >= today,
                or_(
                    select(DealCache.id)
                    .where(DealCache.fetch_id == CircularFetch.id)
                    .exists(),
                    # A parked batch is in flight — don't re-capture and
                    # re-pay while it's queued (the sweep clears stale ones).
                    CircularFetch.pending_batch_id.isnot(None),
                ),
            )
            .order_by(CircularFetch.fetched_at.desc())
        )

    async def process_combo(
        self, db: AsyncSession, chain: SupportedChain, region_key: str
    ) -> dict:
        """Fetch + extract one chain×region circular; returns a summary dict."""
        slug = chain.chain_slug
        # Structured chains (P38 C6) parse deals directly — no pages, no vision.
        if deal_fetcher.strategy_for(chain) == "structured":
            return await self._process_structured(db, chain, region_key)
        fetcher = deal_fetcher.CircularFetcher()
        fetch = await fetcher.fetch_circular(db, chain, region_key)
        await db.commit()
        if fetch.status == "failed":
            return {
                "chain": slug, "region": region_key, "status": "failed",
                "pages": 0, "deals": 0, "matched": 0, "error": fetch.error_message,
            }
        fetch_id = fetch.id
        try:
            result = await self.process_circular(db, fetch_id)
        except Exception as exc:  # noqa: BLE001 — extraction died (API/billing/
            # network): the fetch must not survive as valid or it blocks every
            # re-attempt until the flyer expires while serving zero deals.
            await db.rollback()
            failed = await db.get(CircularFetch, fetch_id)
            if failed is not None:
                failed.status = "failed"
                failed.error_message = (
                    f"extraction failed: {type(exc).__name__}: {exc}"
                )[:1000]
                await db.commit()
            return {
                "chain": slug, "region": region_key, "status": "failed",
                "pages": 0, "deals": 0, "matched": 0,
                "error": f"extraction failed: {type(exc).__name__}: {exc}",
            }
        await db.commit()
        out = {
            "chain": slug, "region": region_key, "status": fetch.status,
            "pages": result["pages"], "deals": result["deals"],
            "matched": result["matched"], "regular_price": result["regular_price"],
        }
        if result.get("pending_batch"):
            out["pending_batch"] = result["pending_batch"]
        return out

    async def _process_structured(
        self, db: AsyncSession, chain: SupportedChain, region_key: str
    ) -> dict:
        """Structured strategy: raw deal dicts -> deal_cache, vision skipped."""
        fetch, raw_deals = await deal_fetcher.fetch_structured_deals(
            db, chain, region_key
        )
        if fetch.status == "failed":
            await db.commit()
            return {
                "chain": chain.chain_slug, "region": region_key,
                "status": "failed", "pages": 0, "deals": 0, "matched": 0,
                "error": fetch.error_message,
            }
        await ingredient_matcher.preload(db)
        today = date.today()
        stale = delete(DealCache).where(
            DealCache.valid_to < today, DealCache.region_key == region_key
        )
        await db.execute(stale)
        rows: list[DealCache] = []
        matched = 0
        for raw in raw_deals:
            row = self._build_deal(
                raw, chain_id=chain.id, fetch_id=fetch.id,
                valid_from=fetch.valid_from, valid_to=fetch.valid_to,
                page_number=0, region_key=region_key,
            )
            if row is None:
                continue
            if row.matched_ingredient_id is not None:
                matched += 1
            rows.append(row)
        db.add_all(rows)
        await db.commit()
        return {
            "chain": chain.chain_slug, "region": region_key,
            "status": "success", "pages": 0, "deals": len(rows),
            "matched": matched, "regular_price": 0,
        }

    async def activate_region(
        self, db: AsyncSession, chain_id: int, region_key: str, zip_code: str | None = None
    ) -> dict:
        """Lazy activation: ensure a chain×region has fresh deals.

        No-op if a valid fetch already exists. If the chain has no working source
        yet, logs demand to ``store_requests`` instead of fetching.
        """
        chain = await db.get(SupportedChain, chain_id)
        if chain is None:
            return {"status": "unknown_chain"}
        today = date.today()
        if await self._combo_has_valid_fetch(db, chain_id, region_key, today):
            return {"chain": chain.chain_slug, "region": region_key, "status": "skipped"}
        if chain.deals_status != "active":
            # Demand wiring (P38 D7): log the demand, then immediately try to
            # EARN the activation — fingerprint the chain's weekly ad, resolve
            # a strategy, fetch, extract. Success flips deals_status to active
            # and the user's "coming soon" badge resolves on their next load;
            # failure keeps the evidence for manual review, and the manual
            # circular-upload endpoint remains the final fallback.
            await regions.log_store_request(
                db, chain_id=chain_id, chain_slug=chain.chain_slug,
                region_key_val=region_key, zip_code=zip_code,
            )
            await db.commit()
            return await self.attempt_activation(db, chain, region_key)
        return await self.process_combo(db, chain, region_key)

    async def attempt_activation(
        self, db: AsyncSession, chain: SupportedChain, region_key: str
    ) -> dict:
        """Try to activate a pending chain end-to-end (P38 D7).

        fingerprint -> strategy -> fetch -> extract. Success flips
        ``deals_status`` to 'active'; failure records the evidence on the
        chain row and leaves it pending_source. Never raises.
        """
        from app.services import circular_probe  # local: avoid import cycle

        slug = chain.chain_slug
        try:
            await circular_probe.ensure_default_profiles(db)
            # Fingerprint when we don't already know the platform.
            if not chain.platform:
                async with httpx.AsyncClient(
                    headers={"User-Agent": circular_probe.UA},
                    follow_redirects=True, timeout=15.0,
                ) as client:
                    url, platform, evidence = (
                        await circular_probe.discover_and_fingerprint(client, chain)
                    )
                chain.platform = platform
                chain.platform_evidence = evidence[:2000]
                # A freshly-resolved weekly-ad URL beats a stale recorded one
                # (H Mart's recorded /weeklyad 404s; discovery finds the live
                # /weekly-ads) — this chain is pending, so nothing depends on
                # the old value.
                if url:
                    chain.source_url = url
                if chain.source_type in (None, "", "chain_site"):
                    strategy = circular_probe.STRATEGY_FOR_PLATFORM.get(platform)
                    if strategy:
                        chain.source_type = strategy
                await db.commit()

            result = await self.process_combo(db, chain, region_key)
            if result.get("pending_batch"):
                # Extraction is parked at Anthropic; the scheduler sweep
                # finishes the activation when the batch ends. Not a failure.
                logger.info(
                    "Demand activation for %s awaiting batch %s",
                    slug, result["pending_batch"],
                )
                result["activated"] = False
                return result
            if result.get("status") in ("success", "partial") and result.get(
                "deals", 0
            ) > 0:
                chain.deals_status = "active"
                await db.commit()
                logger.info(
                    "Demand activation SUCCEEDED for %s (%s): %s deals",
                    slug, region_key, result.get("deals"),
                )
                result["activated"] = True
                return result
            # Keep the failure evidence for manual review.
            why = result.get("error") or (
                "no deals extracted" if not result.get("deals")
                else result.get("status")
            )
            chain.platform_evidence = (
                f"{chain.platform_evidence or ''} | activation failed: {why}"
            )[:2000]
            await db.commit()
            result["activated"] = False
            return result
        except Exception as exc:  # noqa: BLE001 — demand activation is best-effort
            await db.rollback()
            logger.warning("Demand activation errored for %s: %s", slug, exc)
            try:
                chain.platform_evidence = (
                    f"{chain.platform_evidence or ''} | activation error: "
                    f"{type(exc).__name__}: {exc}"
                )[:2000]
                await db.commit()
            except Exception:  # noqa: BLE001
                await db.rollback()
            return {
                "chain": slug, "region": region_key,
                "status": "pending_source", "activated": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

    async def run_pipeline(
        self,
        db: AsyncSession,
        chain_slugs: list[str] | None = None,
        *,
        dry_run: bool = False,
    ) -> list[dict]:
        """Cron entry: refresh DISTINCT chain×region combos that have ≥1 user_store.

        Combos with no users are never enumerated (skipped-dormant). Combos with a
        still-valid fetch are skipped; pending-source combos log demand instead of
        fetching. ``dry_run`` lists the active combos without fetching.
        """
        today = date.today()
        # Active combos = distinct (chain_id, region_key) a real user saved.
        combo_q = (
            select(
                StoreLocation.chain_id,
                StoreLocation.region_key,
                SupportedChain.chain_slug,
            )
            .join(UserStore, UserStore.store_location_id == StoreLocation.id)
            .join(SupportedChain, SupportedChain.id == StoreLocation.chain_id)
            .where(StoreLocation.region_key.isnot(None))
            .distinct()
        )
        if chain_slugs:
            combo_q = combo_q.where(SupportedChain.chain_slug.in_(chain_slugs))
        combos = (await db.execute(combo_q)).all()

        summary: list[dict] = []
        for chain_id, region_key, slug in combos:
            if dry_run:
                summary.append(
                    {"chain": slug, "region": region_key, "status": "would_refresh"}
                )
                continue
            try:
                chain = await db.get(SupportedChain, chain_id)
                if chain is None:
                    continue
                if chain.deals_status != "active" and not chain.source_url:
                    await regions.log_store_request(
                        db, chain_id=chain_id, chain_slug=slug, region_key_val=region_key
                    )
                    await db.commit()
                    summary.append(
                        {"chain": slug, "region": region_key, "status": "pending_source"}
                    )
                    continue
                if await self._combo_has_valid_fetch(db, chain_id, region_key, today):
                    summary.append(
                        {"chain": slug, "region": region_key, "status": "skipped"}
                    )
                    continue
                # Cost-bleed guard (freshness-audit finding): a page that
                # captures fine but extracts ZERO deals (Patel's multi-region
                # gallery with no local flyer this week) must not be
                # re-captured and re-paid every cycle. One attempt per ~2 days
                # on the cron; explicit demand activation can still force it.
                recent_empty = await db.scalar(
                    select(CircularFetch).where(
                        CircularFetch.chain_id == chain_id,
                        CircularFetch.region_key == region_key,
                        CircularFetch.status.in_(("success", "partial")),
                        CircularFetch.pending_batch_id.is_(None),
                        CircularFetch.fetched_at
                        > datetime.now(timezone.utc) - timedelta(hours=47),
                        ~select(DealCache.id)
                        .where(DealCache.fetch_id == CircularFetch.id)
                        .exists(),
                    )
                )
                if recent_empty is not None:
                    summary.append(
                        {"chain": slug, "region": region_key,
                         "status": "empty_recent_skip"}
                    )
                    continue
                summary.append(await self.process_combo(db, chain, region_key))
            except Exception as exc:  # noqa: BLE001 - isolate per-combo failures
                await db.rollback()
                summary.append(
                    {
                        "chain": slug, "region": region_key, "status": "error",
                        "pages": 0, "deals": 0, "matched": 0,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        return summary


    async def collect_pending_batches(self, db: AsyncSession) -> list[dict]:
        """Scheduler sweep: finish extractions whose Batches-API job outlived
        the in-process ceiling. An ended batch inserts its deals (superseding
        the region's older rows) and completes any interrupted activation; a
        batch still queued after 26h marks the fetch failed so the combo
        becomes re-fetchable."""
        fetches = (
            (
                await db.execute(
                    select(CircularFetch).where(
                        CircularFetch.pending_batch_id.isnot(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        if not fetches:
            return []
        await ingredient_matcher.preload(db)
        out: list[dict] = []
        for fetch in fetches:
            bid = fetch.pending_batch_id
            try:
                batch = await self._client.messages.batches.retrieve(bid)
            except Exception as exc:  # noqa: BLE001 — unretrievable = give up
                fetch.pending_batch_id = None
                fetch.status = "failed"
                fetch.error_message = f"pending batch unretrievable: {exc}"[:1000]
                await db.commit()
                out.append({"fetch": fetch.id, "batch": bid, "status": "lost"})
                continue
            if batch.processing_status != "ended":
                age = datetime.now(timezone.utc) - fetch.fetched_at
                if age > timedelta(hours=26):
                    fetch.pending_batch_id = None
                    fetch.status = "failed"
                    fetch.error_message = "batch never finished within 26h"
                    await db.commit()
                    out.append({"fetch": fetch.id, "batch": bid, "status": "expired"})
                else:
                    out.append(
                        {"fetch": fetch.id, "batch": bid, "status": "still_processing"}
                    )
                continue

            chain = await db.get(SupportedChain, fetch.chain_id)
            deals_by_page: dict[int, list[dict]] = {}
            with ai_metering.metering(
                "circular", circular_fetch_id=fetch.id
            ) as _cost_events:
                async for entry in await self._client.messages.batches.results(bid):
                    pn, deals = self._parse_batch_entry(entry)
                    if pn is not None:
                        deals_by_page[pn] = deals
            rows: list[DealCache] = []
            matched = 0
            for pn, deals in deals_by_page.items():
                for raw in deals:
                    row = self._build_deal(
                        raw, chain_id=fetch.chain_id, fetch_id=fetch.id,
                        valid_from=fetch.valid_from, valid_to=fetch.valid_to,
                        page_number=pn, region_key=fetch.region_key,
                    )
                    if row is None:
                        continue
                    if row.matched_ingredient_id is not None:
                        matched += 1
                    rows.append(row)
            if rows:
                supersede = delete(DealCache).where(
                    DealCache.chain_id == fetch.chain_id,
                    DealCache.fetch_id != fetch.id,
                )
                if fetch.region_key is not None:
                    supersede = supersede.where(
                        DealCache.region_key == fetch.region_key
                    )
                await db.execute(supersede)
                db.add_all(rows)
            else:
                fetch.status = "failed"
                fetch.error_message = "parked batch ended with no extractable deals"
            fetch.pending_batch_id = None
            await ai_metering.persist_events(db, _cost_events)
            if chain is not None and rows and chain.deals_status != "active":
                chain.deals_status = "active"
                logger.info(
                    "Deferred activation completed for %s: %d deals collected "
                    "from parked batch %s", chain.chain_slug, len(rows), bid,
                )
            await db.commit()
            logger.info(
                "Collected parked batch %s for fetch %s: %d deals (%d matched)",
                bid, fetch.id, len(rows), matched,
            )
            out.append({
                "fetch": fetch.id, "batch": bid, "status": "collected",
                "deals": len(rows), "matched": matched,
            })
        return out


async def activate_region_bg(chain_id: int, region_key: str, zip_code: str | None = None) -> None:
    """Background-task entry for lazy activation (own DB session)."""
    async with AsyncSessionLocal() as db:
        try:
            await CircularExtractor().activate_region(db, chain_id, region_key, zip_code)
        except Exception:  # noqa: BLE001 - background best-effort
            logger.exception("activate_region_bg failed for %s/%s", chain_id, region_key)
