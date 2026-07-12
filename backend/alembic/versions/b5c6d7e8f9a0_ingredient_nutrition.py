"""ingredient_master deterministic nutrition (Prompt 28 B)

Revision ID: b5c6d7e8f9a0
Revises: a3b4c5d6e7f8
Create Date: 2026-07-12

Adds per-100g USDA macros + a grams-per-typical-unit conversion factor to
ingredient_master so runtime nutrition is COMPUTED from real quantities rather
than trusted from the model's estimate.
"""

from alembic import op
import sqlalchemy as sa


revision = "b5c6d7e8f9a0"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ingredient_master", sa.Column("usda_fdc_id", sa.Integer()))
    op.add_column("ingredient_master", sa.Column("nutrition_source", sa.String(20)))
    op.add_column("ingredient_master", sa.Column("kcal_per_100g", sa.Float()))
    op.add_column("ingredient_master", sa.Column("protein_g_per_100g", sa.Float()))
    op.add_column("ingredient_master", sa.Column("carbs_g_per_100g", sa.Float()))
    op.add_column("ingredient_master", sa.Column("fat_g_per_100g", sa.Float()))
    op.add_column("ingredient_master", sa.Column("fiber_g_per_100g", sa.Float()))
    op.add_column(
        "ingredient_master", sa.Column("grams_per_typical_unit", sa.Float())
    )


def downgrade() -> None:
    for col in (
        "grams_per_typical_unit",
        "fiber_g_per_100g",
        "fat_g_per_100g",
        "carbs_g_per_100g",
        "protein_g_per_100g",
        "kcal_per_100g",
        "nutrition_source",
        "usda_fdc_id",
    ):
        op.drop_column("ingredient_master", col)
