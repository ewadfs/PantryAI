"""ZIP-based store discovery (Prompt 24 B).

With a GOOGLE_PLACES_API_KEY set, geocodes the ZIP and runs a Places text search
per active chain (10-mi bias), upserts the results as store_locations (deduped on
google_place_id), and returns them sorted by distance with a per-chain
has_deals_source flag. Results are cached per ZIP for 30 days.

Without a key it falls back to the seeded catalog (current behavior).
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.store import StoreLocation, SupportedChain, ZipDiscoveryCache
from app.services import regions

_RADIUS_MILES = 10.0
_CACHE_DAYS = 30
_PLACES_CONCURRENCY = 8
_EARTH_MILES = 3958.8


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.pi / 180
    a = (
        0.5
        - math.cos((lat2 - lat1) * p) / 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    )
    return 2 * _EARTH_MILES * math.asin(math.sqrt(a))


async def _geocode_zip(client: httpx.AsyncClient, zip_code: str) -> tuple[float, float] | None:
    r = await client.get(
        "https://maps.googleapis.com/maps/api/geocode/json",
        params={"address": zip_code, "key": settings.google_places_api_key},
    )
    data = r.json()
    results = data.get("results") or []
    if not results:
        return None
    loc = results[0]["geometry"]["location"]
    return float(loc["lat"]), float(loc["lng"])


async def _places_for_chain(
    client: httpx.AsyncClient, chain: SupportedChain, lat: float, lng: float
) -> list[dict]:
    """Text search for one chain near (lat,lng). Returns raw place dicts."""
    body = {
        "textQuery": chain.google_places_query or chain.chain_name,
        "maxResultCount": 5,
        "locationBias": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": _RADIUS_MILES * 1609.34,
            }
        },
    }
    try:
        r = await client.post(
            "https://places.googleapis.com/v1/places:searchText",
            json=body,
            headers={
                "X-Goog-Api-Key": settings.google_places_api_key,
                "X-Goog-FieldMask": (
                    "places.id,places.displayName,places.formattedAddress,"
                    "places.location,places.addressComponents"
                ),
            },
        )
        return r.json().get("places", []) or []
    except (httpx.HTTPError, ValueError):
        return []


def _addr_component(place: dict, kind: str) -> str | None:
    for c in place.get("addressComponents", []):
        if kind in (c.get("types") or []):
            return c.get("shortText") or c.get("longText")
    return None


async def _discover_places(db: AsyncSession, zip_code: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        center = await _geocode_zip(client, zip_code)
        if center is None:
            return []
        clat, clng = center
        chains = (
            (
                await db.execute(
                    select(SupportedChain).where(SupportedChain.is_active.is_(True))
                )
            )
            .scalars()
            .all()
        )
        sem = asyncio.Semaphore(_PLACES_CONCURRENCY)

        async def _one(ch):
            async with sem:
                return ch, await _places_for_chain(client, ch, clat, clng)

        pairs = await asyncio.gather(*(_one(ch) for ch in chains))

    out: list[dict] = []
    for chain, places in pairs:
        for p in places:
            loc = p.get("location") or {}
            plat, plng = loc.get("latitude"), loc.get("longitude")
            state = _addr_component(p, "administrative_area_level_1")
            city = _addr_component(p, "locality")
            pzip = _addr_component(p, "postal_code")
            rkey = regions.region_key(chain.chain_slug, state)
            values = {
                "chain_id": chain.id,
                "store_name": (p.get("displayName") or {}).get("text"),
                "address": p.get("formattedAddress"),
                "city": city,
                "state": (state or None) and state[:2],
                "zip_code": pzip,
                "latitude": plat,
                "longitude": plng,
                "region_key": rkey,
                "google_place_id": p.get("id"),
                "is_active": True,
            }
            stmt = insert(StoreLocation).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["google_place_id"],
                set_={
                    "store_name": stmt.excluded.store_name,
                    "address": stmt.excluded.address,
                    "city": stmt.excluded.city,
                    "state": stmt.excluded.state,
                    "zip_code": stmt.excluded.zip_code,
                    "latitude": stmt.excluded.latitude,
                    "longitude": stmt.excluded.longitude,
                    "region_key": stmt.excluded.region_key,
                },
            ).returning(StoreLocation.id)
            sid = await db.scalar(stmt)
            dist = (
                _haversine(clat, clng, float(plat), float(plng))
                if plat is not None and plng is not None
                else None
            )
            out.append(
                {
                    "id": sid,
                    "store_name": values["store_name"],
                    "address": values["address"],
                    "city": city,
                    "state": values["state"],
                    "zip_code": pzip,
                    "chain_id": chain.id,
                    "chain_name": chain.chain_name,
                    "chain_slug": chain.chain_slug,
                    "distance_miles": round(dist, 1) if dist is not None else None,
                    "has_deals_source": chain.deals_status == "active",
                    "deals_status": chain.deals_status,
                }
            )
    await db.commit()
    out.sort(key=lambda s: (s["distance_miles"] is None, s["distance_miles"] or 0))
    return out


async def _fallback_catalog(db: AsyncSession) -> list[dict]:
    """No Places key: return the seeded store catalog (current behavior)."""
    rows = (
        await db.execute(
            select(StoreLocation, SupportedChain)
            .join(SupportedChain, StoreLocation.chain_id == SupportedChain.id)
            .where(StoreLocation.is_active.is_(True))
            .order_by(SupportedChain.chain_name, StoreLocation.store_name)
        )
    ).all()
    return [
        {
            "id": loc.id,
            "store_name": loc.store_name,
            "address": loc.address,
            "city": loc.city,
            "state": loc.state,
            "zip_code": loc.zip_code,
            "chain_id": chain.id,
            "chain_name": chain.chain_name,
            "chain_slug": chain.chain_slug,
            "distance_miles": None,
            "has_deals_source": chain.deals_status == "active",
            "deals_status": chain.deals_status,
        }
        for loc, chain in rows
    ]


async def discover(db: AsyncSession, zip_code: str) -> tuple[str, list[dict]]:
    """Return (source, stores). source is 'places' or 'catalog'."""
    if not settings.google_places_api_key:
        return "catalog", await _fallback_catalog(db)

    # 30-day per-ZIP cache.
    cached = await db.get(ZipDiscoveryCache, zip_code)
    fresh_after = datetime.now(timezone.utc) - timedelta(days=_CACHE_DAYS)
    if cached is not None and cached.created_at >= fresh_after and cached.payload:
        return "places", list(cached.payload)

    stores = await _discover_places(db, zip_code)
    if not stores:
        return "catalog", await _fallback_catalog(db)

    stmt = insert(ZipDiscoveryCache).values(zip_code=zip_code, payload=stores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["zip_code"],
        set_={"payload": stmt.excluded.payload, "created_at": datetime.now(timezone.utc)},
    )
    await db.execute(stmt)
    await db.commit()
    return "places", stores
