"""End-to-end smoke test for the pantry scan flow.

1. Download an open-license fridge-interior photo into ./test_data/ (once).
2. Run the full in-process scan pipeline (R2 upload -> Claude Vision -> ingredient
   match -> persist) and print the detected items.
3. Exercise the confirm + list HTTP endpoints with a minted test token, against a
   running server (default http://localhost:8000). Skips gracefully if the server
   isn't up, printing the equivalent curl commands.

Run from the backend/ directory (server running in another terminal):
    .venv/Scripts/python.exe scripts/test_pantry_scan.py
"""

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import httpx

from app.database import AsyncSessionLocal
from app.models.user import User
from app.services import vision
from scripts.make_test_token import DEFAULT_EMAIL, DEFAULT_SUB, make_token

BASE_URL = "http://localhost:8000"
TEST_DATA = pathlib.Path(__file__).resolve().parent.parent / "test_data"
IMAGE_PATH = TEST_DATA / "fridge.jpg"

# Open-license refrigerator-interior photos on Wikimedia Commons. Special:FilePath
# redirects to the current raw file bytes and is stable across re-uploads.
_UA = "PantryAI-test/1.0 (https://github.com/ewadfs/PantryAI)"
_IMAGE_CANDIDATES = [
    "https://commons.wikimedia.org/wiki/Special:FilePath/Open%20refrigerator%20with%20food%20at%20night.jpg",
    "https://commons.wikimedia.org/wiki/Special:FilePath/Inside%20domestic%20refrigerator.JPG",
    "https://commons.wikimedia.org/wiki/Special:FilePath/LG%20refrigerator%20interior.jpg",
]


def ensure_test_image() -> bytes:
    """Return the test image bytes, downloading one if not already cached."""
    TEST_DATA.mkdir(exist_ok=True)
    if IMAGE_PATH.exists() and IMAGE_PATH.stat().st_size > 0:
        return IMAGE_PATH.read_bytes()

    with httpx.Client(
        headers={"User-Agent": _UA}, follow_redirects=True, timeout=30.0
    ) as client:
        for url in _IMAGE_CANDIDATES:
            try:
                resp = client.get(url)
                resp.raise_for_status()
                if resp.content:
                    IMAGE_PATH.write_bytes(resp.content)
                    print(f"Downloaded test image from {url} "
                          f"({len(resp.content)} bytes)")
                    return resp.content
            except httpx.HTTPError as exc:
                print(f"  (candidate failed: {url} -> {exc})")

    raise SystemExit(
        f"Could not download a test image. Drop one at {IMAGE_PATH} and re-run."
    )


async def get_or_create_test_user(db) -> User:
    """Fetch-or-create the user the test token authenticates as."""
    from sqlalchemy import select

    user = await db.scalar(
        select(User).where(User.supabase_user_id == DEFAULT_SUB)
    )
    if user is None:
        user = User(supabase_user_id=DEFAULT_SUB, email=DEFAULT_EMAIL)
        db.add(user)
        await db.flush()
    return user


async def run_scan(image_bytes: bytes) -> dict:
    async with AsyncSessionLocal() as db:
        user = await get_or_create_test_user(db)
        result = await vision.process_pantry_scan(db, user.id, [image_bytes])
        await db.commit()
    return result


def print_items(result: dict) -> None:
    print(f"\nScan #{result['scan_id']} — {len(result['items'])} items detected "
          f"from {result['photo_count']} photo(s)\n")
    print(f"{'name':<28}{'qty':<14}{'category':<12}{'fresh':<10}"
          f"{'conf':<6}{'match':<6}{'expiry':<12}")
    print("-" * 88)
    for it in result["items"]:
        print(f"{(it['name'] or '')[:27]:<28}"
              f"{(it.get('quantity_estimate') or '')[:13]:<14}"
              f"{(it.get('category') or '')[:11]:<12}"
              f"{(it.get('freshness') or '')[:9]:<10}"
              f"{it.get('confidence', 0):<6.2f}"
              f"{it.get('match_confidence', 0):<6.2f}"
              f"{(it.get('estimated_expiry') or '-'):<12}")
    if result["uncertain"]:
        print("\nUncertain:", "; ".join(result["uncertain"]))


def exercise_http(result: dict, token: str) -> None:
    """Confirm the scan and list the pantry over HTTP with the test token."""
    headers = {"Authorization": f"Bearer {token}"}
    confirmed = [
        {
            "name": it["name"],
            "quantity_estimate": it.get("quantity_estimate"),
            "unit": it.get("unit"),
            "category": it.get("category"),
        }
        for it in result["items"]
    ]
    body = {"confirmed": confirmed, "removed": [], "corrections": []}

    try:
        with httpx.Client(base_url=BASE_URL, headers=headers, timeout=30.0) as c:
            r = c.post(f"/api/v1/pantry/scan/{result['scan_id']}/confirm", json=body)
            print(f"\nPOST /confirm -> {r.status_code}: {r.text}")

            r = c.get("/api/v1/pantry")
            print(f"GET  /pantry  -> {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                print(f"  {data['count']} active items in "
                      f"{len(data['categories'])} group(s):")
                for group in data["categories"]:
                    names = ", ".join(i["name"] for i in group["items"])
                    print(f"    [{group['category']}] {names}")
    except httpx.ConnectError:
        print(f"\nServer not reachable at {BASE_URL}; skipping HTTP checks.")
        print("Start it with:  .venv/Scripts/python.exe -m uvicorn app.main:app")
        print("Then run curl, e.g.:")
        print(f'  curl -H "Authorization: Bearer {token}" {BASE_URL}/api/v1/pantry')


def main() -> None:
    image_bytes = ensure_test_image()
    result = asyncio.run(run_scan(image_bytes))
    print_items(result)
    exercise_http(result, make_token())


if __name__ == "__main__":
    main()
