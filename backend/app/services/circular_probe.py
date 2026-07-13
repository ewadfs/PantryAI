"""Probe v2 — weekly-ad discovery + serving-platform fingerprinting (P38 A).

Given a chain, find its weekly-ad page (its recorded ``source_url``, or a
homepage guess + an on-page "weekly ad" link) and fingerprint the platform
serving the flyer from script srcs, iframe hosts, and DOM markers. The
fingerprint decides the fetch strategy:

    flipp / quotient_shoplocal / webstop / freshop_mercatus / redpepper /
    vtex / unknown_js      -> 'headless'  (Playwright worker; pixels only)
    pdf_direct             -> 'pdf'       (render pages, same pipeline)
    static_images          -> 'static_images' (plain GET, same pipeline)

Every platform family carries one :class:`~app.models.store.PlatformProfile`
row of viewer hints (navigation mode + selectors), so one profile serves every
chain fingerprinted onto that platform (P38 B5).
"""

import logging
import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.store import PlatformProfile, SupportedChain

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_TIMEOUT = 15.0

# --------------------------------------------------------------------------- #
# Platform detectors — ordered: first match wins. Each entry is
# (platform, regex over the FULL page html, human-readable family).
# --------------------------------------------------------------------------- #
_DETECTORS: list[tuple[str, re.Pattern, str]] = [
    # RedPepper first: chains often carry BOTH legacy Flipp script markers and
    # a live RedPepper publication iframe (King Kullen does) — an actual
    # embedded viewer outranks script-only markers.
    ("redpepper", re.compile(
        r"(redpepper(?:digital)?\.(?:net|digital))", re.I
    ), "RedPepper Digital publication viewer"),
    ("flipp", re.compile(
        r"(flippenterprise\.net|flipp\.com/|Flipp\.Storefront|flipp-container)", re.I
    ), "Flipp-hosted viewer"),
    ("quotient_shoplocal", re.compile(
        r"(shoplocal\.com|quotient\.com|brand\.shoplocal)", re.I
    ), "Quotient/ShopLocal"),
    ("webstop", re.compile(r"(webstop\.com|webstophost)", re.I), "Webstop"),
    ("freshop_mercatus", re.compile(
        r"(freshop\.com|freshop\.ncrcloud|mercatus\.com|storefront\.mercatus)", re.I
    ), "Freshop/Mercatus grocery platform"),
    ("vtex", re.compile(r"(vtexassets\.com|vtex\.file-manager)", re.I),
     "VTEX commerce SPA"),
]

# Direct PDF flyer links ("weekly ad.pdf", "/circular2026.pdf", ...).
_PDF_LINK_RE = re.compile(
    r"href=\"([^\"]*(?:ad|flyer|circular|weekly|special)[^\"]*\.pdf[^\"]*)\"", re.I
)
_ANY_PDF_RE = re.compile(r"href=\"([^\"]+\.pdf(?:\?[^\"]*)?)\"", re.I)

# Static flyer images served straight in the HTML (2+ = a gallery). The
# keyword may live in the src, class, OR alt (Patel Brothers tags its gallery
# via class="store-promo-img deals" while the srcs are opaque CDN names).
_IMG_TAG_RE = re.compile(r"<img[^>]+>", re.I)
_IMG_SRC_RE = re.compile(
    r"(?:src|data-src)=\"[^\"]+\.(?:jpe?g|png|webp)[^\"]*\"", re.I
)
_IMG_KEYWORD_RE = re.compile(
    r"(flyer|circular|week|promo|special|sale|page[-_]?\d)", re.I
)


def _static_flyer_imgs(html: str) -> int:
    return sum(
        1 for tag in _IMG_TAG_RE.findall(html)
        if _IMG_SRC_RE.search(tag) and _IMG_KEYWORD_RE.search(tag)
    )

# Heavy-JS SPA markers (root div + bundle scripts, no server content).
_JS_APP_RE = re.compile(
    r"(__NEXT_DATA__|id=\"root\"|id=\"app\"|webpack|/_next/static/)", re.I
)

# On a homepage, links that lead to the weekly ad.
_WEEKLY_LINK_RE = re.compile(
    r"(week(?:ly)?[-_ ]?(?:ads?|circulars?|specials?|savers?|flyers?)|"
    r"circulars?|flyers?|store-promotions|savings|specials|deals)", re.I
)
_WEEKLY_LINK_BLOCK_RE = re.compile(
    r"(privacy|terms|career|recipe|blog|catering|app[-_ ]?store)", re.I
)


def fingerprint_response(r: httpx.Response) -> tuple[str, str]:
    """(platform, evidence) for a fetched weekly-ad response — catches direct
    PDF flyers (La Bonita serves the circular AS a PDF) before HTML checks."""
    ct = (r.headers.get("content-type") or "").lower()
    if "pdf" in ct or str(r.url).split("?")[0].lower().endswith(".pdf"):
        return "pdf_direct", f"circular served as a PDF at {r.url}"
    return fingerprint_html(r.text, str(r.url))


def fingerprint_html(html: str, url: str) -> tuple[str, str]:
    """(platform, evidence) for a fetched weekly-ad page."""
    for platform, pattern, family in _DETECTORS:
        m = pattern.search(html)
        if m:
            return platform, f"{family}: matched {m.group(1)!r} on {url}"
    m = _PDF_LINK_RE.search(html) or _ANY_PDF_RE.search(html)
    if m:
        return "pdf_direct", f"direct PDF link {m.group(1)[:120]!r} on {url}"
    n_imgs = _static_flyer_imgs(html)
    if n_imgs >= 2:
        return "static_images", (
            f"{n_imgs} flyer-tagged <img> tags served statically on {url}"
        )
    if _JS_APP_RE.search(html):
        return "unknown_js", f"JS app shell (no recognizable viewer) on {url}"
    return "unknown", f"no viewer markers found on {url}"


def _homepage_guesses(chain: SupportedChain) -> list[str]:
    slug = chain.chain_slug
    variants = dict.fromkeys([
        slug.replace("_", ""), slug.replace("_", "-"), slug,
    ])
    return [f"https://www.{v}.com/" for v in variants]


def _weekly_links(html: str, base_url: str) -> list[str]:
    """Weekly-ad-ish links on a homepage, best first."""
    soup = BeautifulSoup(html, "html.parser")
    seen: dict[str, None] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = " ".join(a.stripped_strings)[:80]
        hay = f"{href} {text}"
        if _WEEKLY_LINK_RE.search(hay) and not _WEEKLY_LINK_BLOCK_RE.search(hay):
            seen.setdefault(urljoin(base_url, href), None)
    return list(seen)[:4]


async def discover_and_fingerprint(
    client: httpx.AsyncClient, chain: SupportedChain
) -> tuple[str | None, str, str]:
    """(weekly_ad_url | None, platform, evidence) for one chain.

    Tries the chain's recorded source_url first, then homepage guesses with an
    on-page weekly-ad link hop. Never downloads flyer pages — index HTML only.
    """
    trail: list[str] = []
    candidates: list[str] = []
    if chain.source_url:
        candidates.append(chain.source_url)

    for url in candidates:
        try:
            r = await client.get(url)
        except httpx.HTTPError as exc:
            trail.append(f"{url}: {type(exc).__name__}")
            continue
        if r.status_code == 200 and r.text:
            platform, evidence = fingerprint_response(r)
            if platform not in ("unknown", "unknown_js"):
                return str(r.url), platform, evidence
            # The recorded source may be a homepage — hop its weekly-ad links.
            for link in _weekly_links(r.text, str(r.url)):
                try:
                    ar = await client.get(link)
                except httpx.HTTPError as exc:
                    trail.append(f"{link}: {type(exc).__name__}")
                    continue
                if ar.status_code != 200 or not ar.text:
                    trail.append(f"{link}: HTTP {ar.status_code}")
                    continue
                lp, le = fingerprint_response(ar)
                if lp not in ("unknown",):
                    return str(ar.url), lp, le
                trail.append(le)
            if platform == "unknown_js":
                return str(r.url), platform, evidence
            trail.append(evidence)
        else:
            trail.append(f"{url}: HTTP {r.status_code}")

    for home in _homepage_guesses(chain):
        try:
            r = await client.get(home)
        except httpx.HTTPError as exc:
            trail.append(f"{home}: {type(exc).__name__}")
            continue
        if r.status_code != 200 or not r.text:
            trail.append(f"{home}: HTTP {r.status_code}")
            continue
        links = _weekly_links(r.text, str(r.url))
        if not links:
            # The homepage itself may host the flyer.
            platform, evidence = fingerprint_response(r)
            if platform not in ("unknown", "unknown_js"):
                return str(r.url), platform, evidence
            trail.append(f"{home}: no weekly-ad link")
            continue
        for link in links:
            try:
                ar = await client.get(link)
            except httpx.HTTPError as exc:
                trail.append(f"{link}: {type(exc).__name__}")
                continue
            if ar.status_code != 200 or not ar.text:
                trail.append(f"{link}: HTTP {ar.status_code}")
                continue
            platform, evidence = fingerprint_html(ar.text, str(ar.url))
            if platform != "unknown":
                return str(ar.url), platform, evidence
            trail.append(evidence)
        break  # one homepage was reachable; don't try more guesses

    return None, "unknown", "; ".join(trail[-4:]) or "unreachable"


# Platform -> fetch strategy (P38 registry).
STRATEGY_FOR_PLATFORM = {
    "flipp": "headless",
    "quotient_shoplocal": "headless",
    "webstop": "headless",
    "freshop_mercatus": "headless",
    "redpepper": "headless",
    "vtex": "headless",
    "unknown_js": "headless",
    "pdf_direct": "pdf",
    "static_images": "static_images",
}

# --------------------------------------------------------------------------- #
# Default per-platform viewer profiles (P38 B5) — one row serves every chain
# on the platform. Selectors verified against live members of each family.
# --------------------------------------------------------------------------- #
DEFAULT_PROFILES: list[dict] = [
    dict(platform="flipp", viewer_mode="scroll",
         frame_url_pattern="flippenterprise.net",
         ready_selector="sfml-storefront, flipp-page, .flipp-container, canvas",
         next_selector=None, page_selector=None, max_pages=16,
         notes="Flipp storefront renders a long-scroll canvas flyer inside an "
               "iframe; segmented capture."),
    dict(platform="quotient_shoplocal", viewer_mode="paginated",
         frame_url_pattern="shoplocal.com",
         ready_selector=".sl-page, .page-container, canvas",
         next_selector="[aria-label*='Next'], .sl-arrow-right, .next-page",
         page_selector=".sl-page, .page-container", max_pages=24,
         notes="Quotient/ShopLocal paginated viewer."),
    dict(platform="webstop", viewer_mode="paginated",
         frame_url_pattern="webstop",
         ready_selector=".flyer-page, .page-image, img",
         next_selector="[aria-label*='Next'], a.next, .icon-chevron-right",
         page_selector=".flyer-page, .page-image", max_pages=24,
         notes="Webstop grocery flyer viewer."),
    dict(platform="freshop_mercatus", viewer_mode="paginated",
         frame_url_pattern=None,
         ready_selector=".fp-page, .weekly-ad-page, canvas, img",
         next_selector="[aria-label*='Next'], .fp-arrow-next, button.next",
         page_selector=".fp-page, .weekly-ad-page", max_pages=24,
         notes="Freshop/Mercatus-class grocery platform viewer."),
    dict(platform="redpepper", viewer_mode="url_pages",
         frame_url_pattern="redpepper",
         ready_selector=".page-layout img, img, canvas",
         next_selector="button.vertical_arrow, [aria-label*='Next']",
         page_selector=".page-layout", max_pages=24,
         notes="RedPepper Digital publication viewer: pages route by URL "
               "(…/publications/{slug}/{n}) — the worker loads each page "
               "number standalone and stops at the first repeat."),
    dict(platform="vtex", viewer_mode="scroll", frame_url_pattern=None,
         ready_selector="img[src*='vtexassets']",
         next_selector=None, page_selector=None, max_pages=12,
         notes="VTEX commerce SPA: weekly-ad page renders flyer images after "
               "hydration; segmented full-page capture."),
    dict(platform="unknown_js", viewer_mode="scroll", frame_url_pattern=None,
         ready_selector=None, next_selector=None, page_selector=None,
         max_pages=10,
         notes="Unrecognized JS viewer: generic segmented full-page capture."),
]


async def ensure_default_profiles(db: AsyncSession) -> None:
    """Upsert the default platform profiles (idempotent) — profiles evolve
    with the code, so existing rows are synced to the current defaults."""
    existing = {
        p.platform: p for p in (
            await db.execute(select(PlatformProfile))
        ).scalars()
    }
    for row in DEFAULT_PROFILES:
        cur = existing.get(row["platform"])
        if cur is None:
            db.add(PlatformProfile(**row))
        else:
            for k, v in row.items():
                setattr(cur, k, v)
    await db.flush()


async def profile_for(
    db: AsyncSession, platform: str | None
) -> PlatformProfile | None:
    if not platform:
        return None
    return (
        await db.execute(
            select(PlatformProfile).where(PlatformProfile.platform == platform)
        )
    ).scalar_one_or_none()
