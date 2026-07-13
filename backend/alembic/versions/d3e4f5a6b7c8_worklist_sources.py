"""Ingest the hand-researched missing-stores worklist (206 chains).

Brandon's curated CSV (scripts/data/missing_stores_worklist.csv) resolves the
weekly-ad URL for most of the pending catalog — the probe's homepage guesses
missed chains whose domain differs from their name (bakersplus.com, aldi.us,
cub.com, …). Per row:

- resolved / banner pattern / partial / needs-review-with-URL: source_url set
  to the researched circular URL (falling back to the homepage), platform
  fingerprint cleared so the next probe/demand-activation re-detects against
  the CORRECT page.
- no ad: has_weekly_circular = false (Trader Joe's policy, Marden's surplus
  model, Sam's Club "Instant Savings", …).
- closed: is_active = false.

The CSV note lands on the chain row for the tracking sheet.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-07-13
"""

import csv
import pathlib

import sqlalchemy as sa
from alembic import op

revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None

_CSV = (
    pathlib.Path(__file__).resolve().parents[2]
    / "scripts" / "data" / "missing_stores_worklist.csv"
)


def _first_url(field: str) -> str | None:
    for part in (field or "").split(";"):
        part = part.strip()
        if part.startswith("http"):
            return part[:500]
    return None


def upgrade() -> None:
    if not _CSV.exists():  # image built without the data file — no-op
        return
    conn = op.get_bind()
    upd = sa.text(
        "UPDATE supported_chains SET source_url = COALESCE(:url, source_url), "
        "platform = NULL, platform_evidence = NULL, "
        "notes = CASE WHEN COALESCE(notes, '') = '' THEN :note "
        "ELSE notes || ' | ' || :note END "
        "WHERE chain_slug = :slug AND deals_status != 'active'"
    )
    no_ad = sa.text(
        "UPDATE supported_chains SET has_weekly_circular = false, "
        "notes = CASE WHEN COALESCE(notes, '') = '' THEN :note "
        "ELSE notes || ' | ' || :note END "
        "WHERE chain_slug = :slug"
    )
    closed = sa.text(
        "UPDATE supported_chains SET is_active = false, "
        "notes = CASE WHEN COALESCE(notes, '') = '' THEN :note "
        "ELSE notes || ' | ' || :note END "
        "WHERE chain_slug = :slug"
    )
    with open(_CSV, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            slug = (row.get("Slug") or "").strip()
            if not slug:
                continue
            status = (row.get("Status") or "").strip().lower()
            note = f"worklist[{status}]: {(row.get('Notes') or '').strip()}"[:500]
            if status == "no ad":
                conn.execute(no_ad, {"slug": slug, "note": note})
                continue
            if status == "closed":
                conn.execute(closed, {"slug": slug, "note": note})
                continue
            url = _first_url(row.get("Weekly ad / circular URL") or "") or (
                _first_url(row.get("Homepage") or "")
            )
            conn.execute(upd, {"slug": slug, "url": url, "note": note})


def downgrade() -> None:
    # Data enrichment — no mechanical downgrade (notes/flags are additive).
    pass
