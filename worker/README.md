# Capture worker

Playwright/Chromium sidecar that turns JS flyer viewers (Flipp, Quotient/
ShopLocal, Webstop, Freshop/Mercatus, RedPepper, VTEX, unknown-JS) into page
images for the deals pipeline. See `main.py` docstring.

## Railway setup

1. New service → this repo → **root directory `/worker`** (Dockerfile build).
2. No public domain needed — private networking is enough.
3. On the **API** service set:
   - `HEADLESS_WORKER_URL=http://<worker-private-domain>:8080`
4. Optional worker env: `CAPTURE_SETTLE_MS` (default 4000).

The refresh pipeline dispatches headless fetches here; if the worker is down
or a target breaks, the fetch records `failed`, the chain stays/returns to
`pending_source`, and the cron run continues — never crashes.

## Local dev

```bash
pip install -r requirements.txt && playwright install chromium
uvicorn main:app --port 8080
```

Sandbox note: behind a CONNECT-only MITM proxy set `CAPTURE_PROXY` and
`CAPTURE_RELAY_FETCH=1` (routes browser requests through the Node fetcher);
`CAPTURE_CHROMIUM_PATH` points at a pre-installed Chromium.
