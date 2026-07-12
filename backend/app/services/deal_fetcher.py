"""Circular fetcher — pull weekly-ad flyer pages and stash them in R2.

Discovery (see supported_chains.notes): the aggregator **weeklyadnextweek.com**
serves every supported chain's circular as a gallery of numbered flyer pages
hosted on ``weeklyadpreview.com/images/ADs/{Folder}/{n}.webp``. Those numbered
images are the real flyer pages; the chain logo and author headshot are the only
other images on the page and are filtered out by the URL pattern.

Per-chain strategy is just the aggregator path; unknown chains fall back to a
slugified guess against the same aggregator.
"""

import asyncio
import re
from datetime import date, timedelta

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.deal import CircularFetch
from app.models.store import SupportedChain
from app.services import storage

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

        Network/parse failures are captured as a ``failed`` fetch row rather than
        raised, so one bad chain can't abort the pipeline. ``region_key`` tags the
        fetch so its deals stay isolated to the user's region.
        """
        today = date.today()
        source_url = self._source_url(chain)
        valid_from, valid_to = _validity_window(chain.circular_refresh_day, today)

        status = "failed"
        error_message: str | None = None
        keys: list[str] = []

        try:
            headers = {"User-Agent": _USER_AGENT}
            async with httpx.AsyncClient(
                headers=headers, follow_redirects=True, timeout=_TIMEOUT
            ) as client:
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

            iso = today.isoformat()
            failures = 0
            for item in downloaded:
                if item is None:
                    failures += 1
                    continue
                n, content = item
                key = f"circulars/{chain.chain_slug}/{iso}/page_{n}.jpg"
                await storage.upload_image(content, key, _media_type(content))
                keys.append(key)

            if not keys:
                status = "failed"
                error_message = "All page downloads failed."
            elif failures:
                status = "partial"
                error_message = f"{failures} of {len(page_urls)} pages failed."
            else:
                status = "success"
                self._record_source(chain, source_url)
        except Exception as exc:  # noqa: BLE001 - captured onto the fetch row
            status = "failed"
            error_message = f"{type(exc).__name__}: {exc}"

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

    @staticmethod
    def _record_source(chain: SupportedChain, source_url: str) -> None:
        """Stamp the working source onto supported_chains.notes."""
        base = re.sub(r"\s*\|\s*circular_source=\S+", "", chain.notes or "").strip()
        marker = f"circular_source={source_url}"
        chain.notes = f"{base} | {marker}" if base else marker
