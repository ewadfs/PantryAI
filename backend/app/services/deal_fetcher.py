"""Circular fetcher — pull weekly-ad flyer pages and stash them in R2.

Strategy registry (P38): every chain resolves to ONE fetch strategy via
``source_type`` (with the probe-v2 ``platform`` fingerprint refining it):

- ``aggregator``     — weeklyadnextweek.com gallery of numbered flyer pages
                       (the original path; unknown chains still try it).
- ``headless``       — the Playwright worker service captures each viewer
                       page as pixels (Flipp/Quotient/Webstop/Freshop/
                       RedPepper/VTEX/unknown-JS). No DOM parsing of deals.
- ``pdf``            — direct PDF flyer -> pdf-to-images -> same pipeline.
- ``static_images``  — flyer images served straight in the page HTML.
- ``structured``     — chain-specific data parse (registry slot; Whole Foods
                       pattern) that writes deals directly, no vision pass.
- ``chain_site``     — legacy hint; treated as headless.

All strategies end the same way: page images uploaded to R2 and one
``circular_fetches`` row — the vision extraction downstream is identical.
Failures are captured as a ``failed`` fetch row (the caller degrades the
chain to pending_source); they never crash the run.
"""

import asyncio
import base64
import io
import logging
import re
from datetime import date, timedelta
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.deal import CircularFetch
from app.models.store import SupportedChain
from app.services import circular_probe, storage

logger = logging.getLogger(__name__)

_SOURCE_BASE = "https://weeklyadnextweek.com"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_TIMEOUT = 20.0
_MAX_PAGES = 25

# chain_slug -> aggregator path on weeklyadnextweek.com
_PATHS = {
    "shoprite": "shoprite",
    "stop_and_shop": "stopandshop",
    "lidl": "lidl",
}

# Flyer-page images live under weeklyadpreview.com/images/ADs/<folder>/<n>.webp
_PAGE_IMG_RE = re.compile(
    r"weeklyadpreview\.com/images/ADs/[^/\"']+/(\d+)\.(?:webp|jpe?g|png)", re.I
)

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _media_type(image_bytes: bytes) -> str:
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "image/webp"


def _validity_window(refresh_day: str | None, today: date) -> tuple[date, date]:
    """valid_from = most recent occurrence of ``refresh_day`` (<= today); +6 days."""
    target = _WEEKDAYS.get((refresh_day or "").strip().lower())
    if target is None:
        # Unknown cadence: assume the week started today.
        valid_from = today
    else:
        delta = (today.weekday() - target) % 7
        valid_from = today - timedelta(days=delta)
    return valid_from, valid_from + timedelta(days=6)


def strategy_for(chain: SupportedChain) -> str:
    """Resolve the fetch strategy for a chain (P38 registry)."""
    st = (chain.source_type or "").strip()
    if st == "chain_site":  # legacy hint: the chain's own site needs a browser
        st = circular_probe.STRATEGY_FOR_PLATFORM.get(chain.platform or "", "headless")
    if st in ("headless", "pdf", "static_images", "structured", "aggregator"):
        return st
    if chain.platform:
        return circular_probe.STRATEGY_FOR_PLATFORM.get(chain.platform, "aggregator")
    return "aggregator"


class CircularFetcher:
    """Fetches a chain's circular pages and records a ``circular_fetches`` row."""

    def _source_url(self, chain: SupportedChain) -> str:
        # Prefer the source the probe resolved onto the chain row.
        if chain.source_url:
            return chain.source_url
        path = _PATHS.get(chain.chain_slug, chain.chain_slug.replace("_", "-"))
        return f"{_SOURCE_BASE}/{path}"

    def _extract_page_urls(self, html: str) -> list[str]:
        """Return flyer-page image URLs, deduped and ordered by page number."""
        soup = BeautifulSoup(html, "html.parser")
        by_num: dict[int, str] = {}
        for img in soup.find_all("img"):
            for attr in ("src", "data-src", "data-original", "data-lazy"):
                src = img.get(attr)
                if not src:
                    continue
                m = _PAGE_IMG_RE.search(src)
                if m:
                    if src.startswith("//"):
                        src = "https:" + src
                    by_num.setdefault(int(m.group(1)), src)
                    break
        return [by_num[n] for n in sorted(by_num)]

    async def fetch_circular(
        self, db: AsyncSession, chain: SupportedChain, region_key: str | None = None
    ) -> CircularFetch:
        """Fetch + store this chain's circular; always returns a persisted row.

        Dispatches on the chain's resolved strategy (P38 registry). Network,
        worker, and parse failures are captured as a ``failed`` fetch row rather
        than raised, so one bad chain can't abort the pipeline. ``region_key``
        tags the fetch so its deals stay isolated to the user's region.
        """
        today = date.today()
        strategy = strategy_for(chain)
        source_url = self._source_url(chain)
        valid_from, valid_to = _validity_window(chain.circular_refresh_day, today)

        status = "failed"
        error_message: str | None = None
        keys: list[str] = []

        try:
            pages = await self._collect_pages(db, chain, strategy, source_url)
            if not pages:
                raise ValueError(f"No circular pages produced ({strategy}).")

            iso = today.isoformat()
            for n, content in pages:
                key = f"circulars/{chain.chain_slug}/{iso}/page_{n}.jpg"
                await storage.upload_image(content, key, _media_type(content))
                keys.append(key)
            status = "success"
            self._record_source(chain, source_url)
        except Exception as exc:  # noqa: BLE001 - captured onto the fetch row
            status = "failed"
            error_message = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "Circular fetch failed for %s via %s: %s",
                chain.chain_slug, strategy, error_message,
            )

        fetch = CircularFetch(
            chain_id=chain.id,
            fetch_date=today,
            source_url=source_url,
            page_count=len(keys),
            image_keys=keys or None,
            status=status,
            error_message=error_message,
            valid_from=valid_from,
            valid_to=valid_to,
            region_key=region_key,
        )
        db.add(fetch)
        await db.flush()
        return fetch

    async def _collect_pages(
        self, db: AsyncSession, chain: SupportedChain, strategy: str, source_url: str
    ) -> list[tuple[int, bytes]]:
        """Produce ordered (page_number, image_bytes) via the chain's strategy."""
        headers = {"User-Agent": _USER_AGENT}
        async with httpx.AsyncClient(
            headers=headers, follow_redirects=True, timeout=_TIMEOUT
        ) as client:
            if strategy == "headless":
                return await self._pages_headless(db, chain, source_url)
            if strategy == "pdf":
                return await self._pages_pdf(client, source_url)
            if strategy == "static_images":
                return await self._pages_static_images(client, source_url)
            if strategy == "structured":
                raise ValueError(
                    "structured chains write deals directly — use "
                    "fetch_structured_deals()."
                )
            return await self._pages_aggregator(client, source_url)

    # ---- aggregator (the original path) ---------------------------------- #
    async def _pages_aggregator(
        self, client: httpx.AsyncClient, source_url: str
    ) -> list[tuple[int, bytes]]:
        resp = await client.get(source_url)
        resp.raise_for_status()
        page_urls = self._extract_page_urls(resp.text)[:_MAX_PAGES]
        if not page_urls:
            raise ValueError("No circular page images found on source.")

        async def _grab(n: int, url: str) -> tuple[int, bytes] | None:
            try:
                r = await client.get(url)
                r.raise_for_status()
                return (n, r.content) if r.content else None
            except httpx.HTTPError:
                return None

        downloaded = await asyncio.gather(
            *(_grab(n, url) for n, url in enumerate(page_urls, start=1))
        )
        pages = [item for item in downloaded if item is not None]
        if not pages:
            raise ValueError("All page downloads failed.")
        return pages

    # ---- headless (Playwright worker; pixels only, P38 B) ----------------- #
    async def _pages_headless(
        self, db: AsyncSession, chain: SupportedChain, source_url: str
    ) -> list[tuple[int, bytes]]:
        worker = (settings.headless_worker_url or "").rstrip("/")
        if not worker:
            raise ValueError("HEADLESS_WORKER_URL is not configured.")
        profile = await circular_probe.profile_for(db, chain.platform)
        payload = {
            "url": source_url,
            "viewer_mode": profile.viewer_mode if profile else "scroll",
            "frame_url_pattern": profile.frame_url_pattern if profile else None,
            "ready_selector": profile.ready_selector if profile else None,
            "next_selector": profile.next_selector if profile else None,
            "page_selector": profile.page_selector if profile else None,
            "max_pages": min(profile.max_pages if profile else 12, _MAX_PAGES),
        }
        async with httpx.AsyncClient(
            timeout=settings.headless_worker_timeout
        ) as wclient:
            r = await wclient.post(f"{worker}/capture", json=payload)
            r.raise_for_status()
            data = r.json()
        if data.get("status") != "ok" or not data.get("pages"):
            raise ValueError(
                f"worker capture failed: {data.get('error') or 'no pages'}"
            )
        return [
            (n, base64.b64decode(b64))
            for n, b64 in enumerate(data["pages"], start=1)
        ]

    # ---- pdf (direct PDF flyer -> images, P38 C6) -------------------------- #
    async def _pages_pdf(
        self, client: httpx.AsyncClient, source_url: str
    ) -> list[tuple[int, bytes]]:
        r = await client.get(source_url)
        r.raise_for_status()
        content = r.content
        if not content.startswith(b"%PDF"):
            # The URL is a page that links to the flyer PDF — follow the link.
            m = re.search(
                r"href=\"([^\"]*(?:ad|flyer|circular|weekly|special)[^\"]*"
                r"\.pdf[^\"]*)\"", r.text, re.I,
            ) or re.search(r"href=\"([^\"]+\.pdf(?:\?[^\"]*)?)\"", r.text, re.I)
            if not m:
                raise ValueError("No PDF link found on source page.")
            pr = await client.get(urljoin(str(r.url), m.group(1)))
            pr.raise_for_status()
            content = pr.content
            if not content.startswith(b"%PDF"):
                raise ValueError("Linked file is not a PDF.")
        return await asyncio.to_thread(self._render_pdf, content)

    @staticmethod
    def _render_pdf(pdf_bytes: bytes) -> list[tuple[int, bytes]]:
        import pypdfium2 as pdfium  # local import: keep module import light

        doc = pdfium.PdfDocument(pdf_bytes)
        pages: list[tuple[int, bytes]] = []
        try:
            for i in range(min(len(doc), _MAX_PAGES)):
                bitmap = doc[i].render(scale=2.0)
                pil = bitmap.to_pil().convert("RGB")
                buf = io.BytesIO()
                pil.save(buf, format="JPEG", quality=85)
                pages.append((i + 1, buf.getvalue()))
        finally:
            doc.close()
        return pages

    # ---- static images (flyer <img>s in the HTML, e.g. Patel Brothers) ---- #
    async def _pages_static_images(
        self, client: httpx.AsyncClient, source_url: str
    ) -> list[tuple[int, bytes]]:
        r = await client.get(source_url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        urls: list[str] = []
        # Class-hinted flyer galleries first (store-promo / flyer / circular…).
        hint = re.compile(r"(promo|flyer|circular|weekly|special|deal)", re.I)
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            if not re.search(r"\.(?:jpe?g|png|webp)(?:\?|$)", src, re.I):
                continue
            hay = " ".join([src, " ".join(img.get("class") or []),
                            img.get("alt") or ""])
            if hint.search(hay):
                full = urljoin(str(r.url), src)
                if full not in urls:
                    urls.append(full)
        if len(urls) < 1:
            raise ValueError("No static flyer images found on source page.")

        async def _grab(n: int, url: str) -> tuple[int, bytes] | None:
            try:
                ir = await client.get(url)
                ir.raise_for_status()
                # Skip obvious non-flyer thumbnails.
                return (n, ir.content) if len(ir.content) > 30_000 else None
            except httpx.HTTPError:
                return None

        downloaded = await asyncio.gather(
            *(_grab(n, u) for n, u in enumerate(urls[:_MAX_PAGES], start=1))
        )
        pages = [p for p in downloaded if p is not None]
        if not pages:
            raise ValueError("All static flyer image downloads failed.")
        # Renumber densely after skips.
        return [(i + 1, content) for i, (_n, content) in enumerate(pages)]

    @staticmethod
    def _record_source(chain: SupportedChain, source_url: str) -> None:
        """Stamp the working source onto supported_chains.notes."""
        base = re.sub(r"\s*\|\s*circular_source=\S+", "", chain.notes or "").strip()
        marker = f"circular_source={source_url}"
        chain.notes = f"{base} | {marker}" if base else marker


# --------------------------------------------------------------------------- #
# Structured strategy registry (P38 C6) — chain-specific data parses that
# return DEAL DICTS directly (no flyer pages, no vision pass). Each entry maps
# chain_slug -> async fn(client, chain) -> list[dict] in the same shape the
# vision extractor emits (product_name, sale_price, price_unit, category, ...).
# Raising is fine: the caller records a failed fetch and the chain degrades to
# pending_source.
# --------------------------------------------------------------------------- #
async def _structured_whole_foods(
    client: httpx.AsyncClient, chain: SupportedChain
) -> list[dict]:
    """Whole Foods pattern: wholefoodsmarket.com/sales is a Next.js SPA.

    The server-rendered payload carries no deals, so we walk the known data
    routes (buildId-scoped sales JSON, then the wwos API) and parse whichever
    answers. When Amazon withholds all of them (the current public state —
    probed live), we raise with the evidence; the chain stays pending_source
    and the manual-upload path remains the fallback.
    """
    base = "https://www.wholefoodsmarket.com"
    r = await client.get(f"{base}/sales")
    r.raise_for_status()
    trail: list[str] = []
    m = re.search(r'"buildId":"([^"]+)"', r.text)
    candidates = []
    if m:
        candidates.append(f"{base}/_next/data/{m.group(1)}/sales.json")
    candidates += [f"{base}/api/wwos/sales", f"{base}/api/sales"]
    for url in candidates:
        try:
            jr = await client.get(url, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            trail.append(f"{url}: {type(exc).__name__}")
            continue
        if jr.status_code != 200:
            trail.append(f"{url}: HTTP {jr.status_code}")
            continue
        try:
            data = jr.json()
        except ValueError:
            trail.append(f"{url}: not JSON")
            continue
        deals = _parse_wf_sales(data)
        if deals:
            return deals
        trail.append(f"{url}: JSON but no sales items")
    raise ValueError(
        "Whole Foods structured source unavailable: " + "; ".join(trail)
    )


def _parse_wf_sales(data: dict) -> list[dict]:
    """Best-effort walk of a WF sales payload into extractor-shaped dicts."""
    items: list[dict] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            name = node.get("name") or node.get("title")
            price = node.get("salePrice") or node.get("sale_price")
            if price is None and isinstance(node.get("pricing"), dict):
                price = node["pricing"].get("salePrice")
            if name and price:
                items.append({
                    "product_name": str(name),
                    "brand": node.get("brand"),
                    "sale_price": price,
                    "price_unit": node.get("uom") or node.get("unit"),
                    "regular_price": node.get("regularPrice"),
                    "deal_type": "sale",
                    "category": (node.get("category") or "other"),
                    "confidence": 0.95,
                })
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return items


STRUCTURED_REGISTRY: dict = {
    "whole_foods_market": _structured_whole_foods,
    "whole_foods": _structured_whole_foods,
}


async def fetch_structured_deals(
    db: AsyncSession, chain: SupportedChain, region_key: str | None = None
) -> tuple[CircularFetch, list[dict]]:
    """Run a chain's structured parser; returns (fetch_row, raw_deal_dicts).

    Success records a page_count=0 'success' fetch (structured chains publish
    deals without flyer pages); failure records a 'failed' fetch with the
    evidence, exactly like the image strategies.
    """
    today = date.today()
    valid_from, valid_to = _validity_window(chain.circular_refresh_day, today)
    parser = STRUCTURED_REGISTRY.get(chain.chain_slug)
    deals: list[dict] = []
    status, error_message = "failed", None
    if parser is None:
        error_message = f"No structured parser registered for {chain.chain_slug}."
    else:
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": _USER_AGENT}, follow_redirects=True,
                timeout=_TIMEOUT,
            ) as client:
                deals = await parser(client, chain)
            status = "success"
        except Exception as exc:  # noqa: BLE001 - captured onto the fetch row
            error_message = f"{type(exc).__name__}: {exc}"

    fetch = CircularFetch(
        chain_id=chain.id, fetch_date=today,
        source_url=chain.source_url, page_count=0, image_keys=None,
        status=status, error_message=error_message,
        valid_from=valid_from, valid_to=valid_to, region_key=region_key,
    )
    db.add(fetch)
    await db.flush()
    return fetch, deals
