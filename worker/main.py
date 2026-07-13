"""PantryAI headless capture worker (P38 B) — the skeleton key.

A tiny FastAPI service wrapping Playwright/Chromium. The API service posts a
flyer-viewer URL plus per-platform hints; this worker loads the page, waits
for the viewer to render, captures each flyer page as a JPEG, and returns the
images base64-encoded. It never parses deals from the DOM — pixels only; the
API side feeds them to the existing R2 → vision → deal_cache pipeline.

Three navigation modes cover the common viewer families:
- ``paginated``: click the next button in a loop (sane max), screenshotting
  the page container (or the viewer frame's viewport) each step.
- ``scroll``: segmented full-page capture — scroll a viewport at a time and
  screenshot each segment until the page bottom (sane max).
- ``url_pages``: publication viewers that route pages by URL (RedPepper's
  ``…/publications/{slug}/{n}``): resolve the viewer frame's URL, then load
  ``…/1``, ``…/2``, … directly, stopping at the first repeated frame.

Runs as a separate Railway service on Playwright's official Docker base so the
API image stays slim. Failures return {"status": "error", ...}; the caller
records a failed fetch and degrades to pending_source — a broken target never
crashes the refresh run.

Sandbox/dev quirk: set ``CAPTURE_RELAY_FETCH=1`` to route every browser
request through Playwright's Node-side fetcher (needed behind MITM dev
proxies that only accept CONNECT from non-browser clients). Production
leaves it unset. ``CAPTURE_PROXY`` passes an explicit proxy server.
"""

import base64
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field

logger = logging.getLogger("capture-worker")
logging.basicConfig(level=logging.INFO)

_HARD_MAX_PAGES = 30
_NAV_TIMEOUT_MS = 45_000
_SETTLE_MS = int(os.environ.get("CAPTURE_SETTLE_MS", "4000"))
_VIEWPORT = {"width": 1280, "height": 1600}

_pw = None
_browser = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pw, _browser
    _pw = await async_playwright().start()
    launch_kwargs: dict = {}
    exe = os.environ.get("CAPTURE_CHROMIUM_PATH")
    if exe:
        launch_kwargs["executable_path"] = exe
    proxy = os.environ.get("CAPTURE_PROXY")
    if proxy:
        launch_kwargs["proxy"] = {"server": proxy}
    _browser = await _pw.chromium.launch(**launch_kwargs)
    logger.info("Chromium up (proxy=%s relay=%s)", bool(proxy),
                os.environ.get("CAPTURE_RELAY_FETCH") == "1")
    yield
    await _browser.close()
    await _pw.stop()


app = FastAPI(lifespan=lifespan)


class CaptureRequest(BaseModel):
    url: str
    viewer_mode: str = "scroll"  # 'paginated' | 'scroll'
    frame_url_pattern: str | None = None
    ready_selector: str | None = None
    next_selector: str | None = None
    page_selector: str | None = None
    max_pages: int = Field(default=12, ge=1, le=_HARD_MAX_PAGES)
    settle_ms: int | None = None


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "browser": _browser is not None}


async def _relay(route, request):  # dev-proxy relay (see module docstring)
    try:
        resp = await route.fetch()
        await route.fulfill(response=resp)
    except Exception:
        try:
            await route.abort()
        except Exception:
            pass


def _b64(png: bytes) -> str:
    return base64.b64encode(png).decode("ascii")


async def _find_target(page, req: CaptureRequest):
    """The frame hosting the viewer (matched by URL pattern), else the page."""
    if req.frame_url_pattern:
        for frame in page.frames:
            if req.frame_url_pattern in frame.url:
                return frame
    return page.main_frame


async def _capture_paginated(page, target, req: CaptureRequest) -> list[bytes]:
    """Next-button loop. Screenshot the page container (or the frame's owner
    element / viewport) each step; stop when the button disappears/disables."""
    shots: list[bytes] = []
    settle = req.settle_ms or _SETTLE_MS

    async def shoot() -> bytes:
        if req.page_selector:
            for sel in req.page_selector.split(","):
                loc = target.locator(sel.strip()).first
                try:
                    if await loc.count() > 0 and await loc.is_visible():
                        return await loc.screenshot(type="jpeg", quality=80)
                except Exception:  # noqa: BLE001 — fall through to viewport
                    continue
        if target is not page.main_frame and req.frame_url_pattern:
            el = await target.frame_element()
            return await el.screenshot(type="jpeg", quality=80)
        return await page.screenshot(type="jpeg", quality=80)

    shots.append(await shoot())
    for _ in range(req.max_pages - 1):
        clicked = False
        for sel in (req.next_selector or "").split(","):
            sel = sel.strip()
            if not sel:
                continue
            # Viewers render prev before next; among matches (excluding
            # disabled ones) the LAST is the forward control.
            btn = target.locator(f"{sel}:not(.disabled):not([disabled])").last
            try:
                if await btn.count() > 0 and await btn.is_visible() and (
                    await btn.is_enabled()
                ):
                    await btn.click(timeout=3000)
                    clicked = True
                    break
            except Exception:  # noqa: BLE001 — try the next selector
                continue
        if not clicked:
            # Generic fallback: publication viewers near-universally page on
            # ArrowRight. Focus the viewer first (click its center), then
            # press; the identical-shot check below ends the loop when the
            # viewer stops turning.
            try:
                if target is not page.main_frame:
                    el = await target.frame_element()
                    box = await el.bounding_box()
                    if box:
                        await page.mouse.click(
                            box["x"] + box["width"] / 2,
                            box["y"] + min(box["height"] / 2, 500),
                        )
                await page.keyboard.press("ArrowRight")
                clicked = True
            except Exception:  # noqa: BLE001 — no way to advance
                break
        await page.wait_for_timeout(settle)
        shot = await shoot()
        # A page identical to the previous one means navigation stalled.
        if shots and shot == shots[-1]:
            break
        shots.append(shot)
    return shots


_TRAILING_PAGE_RE = None  # set lazily to avoid import-order noise


async def _capture_url_pages(page, target, req: CaptureRequest) -> list[bytes]:
    """URL-routed viewers: the frame URL ends in the page number — load each
    page number directly (standalone) and screenshot. Stops on a repeated
    shot (viewers clamp past-the-end requests to the last page)."""
    import re as _re

    m = _re.match(r"^(.*)/(\d+)(?:[?#].*)?$", target.url if target else "")
    if not m:
        # No page-number route — fall back to click pagination.
        return await _capture_paginated(page, target, req)
    base = m.group(1)
    settle = req.settle_ms or _SETTLE_MS
    # Past-the-end page numbers wrap on some viewers instead of clamping —
    # read the viewer's own page total when it exposes one ("… / 6").
    total = None
    try:
        total = await (target or page.main_frame).evaluate(
            """() => {
              const el = document.querySelector(
                "[class*='max-page'],[class*='total-page'],[class*='page-count']");
              if (!el) return null;
              const m = (el.textContent || '').match(/(\\d+)/);
              return m ? parseInt(m[1], 10) : null;
            }"""
        )
    except Exception:  # noqa: BLE001 — dedupe below still bounds the loop
        total = None
    limit = min(req.max_pages, total) if total else req.max_pages
    shots: list[bytes] = []
    for n in range(1, limit + 1):
        await page.goto(
            f"{base}/{n}", wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS
        )
        for sel in (req.ready_selector or "").split(","):
            sel = sel.strip()
            if not sel:
                continue
            try:
                await page.wait_for_selector(sel, timeout=8000, state="visible")
                break
            except Exception:  # noqa: BLE001 — try the next candidate
                continue
        # Don't screenshot half-painted flyer images (slow CDNs would make
        # consecutive pages look identical and trip the dedupe break).
        try:
            await page.wait_for_function(
                "() => document.images.length > 0 && "
                "Array.from(document.images).every(i => i.complete)",
                timeout=12_000,
            )
        except Exception:  # noqa: BLE001 — settle delay below still applies
            pass
        await page.wait_for_timeout(settle)
        shot = await page.screenshot(type="jpeg", quality=80, full_page=True)
        if shots and shot == shots[-1]:
            break
        shots.append(shot)
    return shots


async def _capture_scroll(page, target, req: CaptureRequest) -> list[bytes]:
    """Segmented long capture: viewport screenshots down the page/frame."""
    shots: list[bytes] = []
    settle = req.settle_ms or _SETTLE_MS
    scroller = target if target is page.main_frame else target
    vh = _VIEWPORT["height"]
    last = None
    for i in range(req.max_pages):
        shot = await page.screenshot(type="jpeg", quality=80)
        if shot == last:
            break
        shots.append(shot)
        last = shot
        at_bottom = await scroller.evaluate(
            "() => (window.innerHeight + window.scrollY) >= "
            "(document.body.scrollHeight - 8)"
        )
        if at_bottom:
            break
        await scroller.evaluate(f"() => window.scrollBy(0, {vh})")
        await page.wait_for_timeout(min(settle, 2500))
    return shots


@app.post("/capture")
async def capture(req: CaptureRequest) -> dict:
    if _browser is None:
        return {"status": "error", "error": "browser not ready"}
    ctx = await _browser.new_context(viewport=_VIEWPORT)
    try:
        if os.environ.get("CAPTURE_RELAY_FETCH") == "1":
            await ctx.route("**/*", _relay)
        page = await ctx.new_page()
        resp = await page.goto(
            req.url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS
        )
        if resp is not None and resp.status >= 400:
            return {"status": "error",
                    "error": f"target returned HTTP {resp.status}"}
        settle = req.settle_ms or _SETTLE_MS
        await page.wait_for_timeout(settle)

        # The viewer frame may attach late — poll for it briefly.
        target = await _find_target(page, req)
        if req.frame_url_pattern and target is page.main_frame:
            for _ in range(10):
                await page.wait_for_timeout(1000)
                target = await _find_target(page, req)
                if target is not page.main_frame:
                    break

        # Wait for the viewer to render INSIDE the target frame: first match
        # of any comma-separated ready-selector candidate, then let images
        # paint. Heavy viewers (Flipp/RedPepper canvases) need the tail wait.
        ready = False
        for sel in (req.ready_selector or "").split(","):
            sel = sel.strip()
            if not sel:
                continue
            try:
                await target.wait_for_selector(sel, timeout=10_000, state="visible")
                ready = True
                break
            except Exception:  # noqa: BLE001 — try the next candidate
                continue
        await page.wait_for_timeout(settle if ready else settle * 2)
        if req.viewer_mode == "url_pages":
            shots = await _capture_url_pages(page, target, req)
        elif req.viewer_mode == "paginated":
            shots = await _capture_paginated(page, target, req)
        else:
            shots = await _capture_scroll(page, target, req)
        if not shots:
            return {"status": "error", "error": "no pages captured"}
        return {
            "status": "ok",
            "mode_used": req.viewer_mode,
            "page_count": len(shots),
            "pages": [_b64(s) for s in shots],
        }
    except Exception as exc:  # noqa: BLE001 — the caller degrades gracefully
        logger.warning("capture failed for %s: %s", req.url, exc)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        await ctx.close()
