"""enforce one default store per user (Prompt 31 hardening)

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-07-12

Makes two default stores for one user impossible at the database level via a
partial unique index UNIQUE(user_id) WHERE is_default. First heals any existing
duplicate defaults by keeping only the most-recently-added one per user.
"""

from alembic import op


revision = "d7e8f9a0b1c2"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Heal any user with >1 default: keep the most-recently-added default only.
    op.execute(
        """
        UPDATE user_stores u
        SET is_default = false
        WHERE u.is_default = true
          AND u.id <> (
            SELECT u2.id FROM user_stores u2
            WHERE u2.user_id = u.user_id AND u2.is_default = true
            ORDER BY u2.added_at DESC, u2.id DESC
            LIMIT 1
          )
        """
    )
    # The DB now forbids a second default per user.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_user_stores_one_default
        ON user_stores (user_id)
        WHERE is_default
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_user_stores_one_default")
