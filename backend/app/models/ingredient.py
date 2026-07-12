from sqlalchemy import Boolean, Float, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IngredientMaster(Base):
    __tablename__ = "ingredient_master"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(
        String(200), unique=True, nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(50))
    typical_unit: Mapped[str | None] = mapped_column(String(30))
    shelf_life_days: Mapped[int | None] = mapped_column(Integer)
    is_pantry_staple: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    common_aliases: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    # --- Deterministic nutrition (Prompt 28 B) ---------------------------- #
    # Per-100g macros sourced from USDA FoodData Central (raw, as-purchased —
    # recipes list raw weights). nutrition_source: 'usda' | 'curated' | None.
    # grams_per_typical_unit converts count/cup/tbsp lines to grams for the
    # compute engine (e.g. 1 egg ≈ 50 g, 1 cup rice ≈ 185 g).
    usda_fdc_id: Mapped[int | None] = mapped_column(Integer)
    nutrition_source: Mapped[str | None] = mapped_column(String(20))
    kcal_per_100g: Mapped[float | None] = mapped_column(Float)
    protein_g_per_100g: Mapped[float | None] = mapped_column(Float)
    carbs_g_per_100g: Mapped[float | None] = mapped_column(Float)
    fat_g_per_100g: Mapped[float | None] = mapped_column(Float)
    fiber_g_per_100g: Mapped[float | None] = mapped_column(Float)
    grams_per_typical_unit: Mapped[float | None] = mapped_column(Float)
