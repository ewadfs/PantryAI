from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Recipe(Base):
    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    # 'concept' (Stage 1 done, details pending) or 'ready' (full details written)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'ready'")
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    difficulty: Mapped[str | None] = mapped_column(String(10))
    # 4-6 defining ingredients from Stage 1; superseded by ingredients_json.
    key_ingredients_json: Mapped[dict | list | None] = mapped_column(JSONB)
    prep_time_min: Mapped[int | None] = mapped_column(Integer)
    cook_time_min: Mapped[int | None] = mapped_column(Integer)
    total_time_min: Mapped[int | None] = mapped_column(Integer)
    servings: Mapped[int | None] = mapped_column(Integer)
    ingredients_json: Mapped[dict | list | None] = mapped_column(JSONB)
    instructions_json: Mapped[dict | list | None] = mapped_column(JSONB)
    nutrition_json: Mapped[dict | list | None] = mapped_column(JSONB)
    why_this_recipe: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    cuisine: Mapped[str | None] = mapped_column(String(50))
    # Store this batch was anchored to (for the weekly store selector / staleness).
    generated_store_name: Mapped[str | None] = mapped_column(String(200))
    # Pinned pantry items this batch was built around ("cook with this").
    pinned_items_json: Mapped[dict | list | None] = mapped_column(JSONB)
    # Ephemeral per-batch direction the user typed ("grill something").
    direction: Mapped[str | None] = mapped_column(String(200))
    # Difficulty tiers this batch was drawn from (subset of easy/medium/hard).
    # NULL means all three; persisted so warm-cache + /latest can reuse it.
    difficulties: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    ai_model: Mapped[str | None] = mapped_column(String(50))
    # Critic pass (Stage 1.5): {score, worst_issues, verdict, fail_rubrics, regenerated}.
    critic_json: Mapped[dict | list | None] = mapped_column(JSONB)
    # Variety signature: {anchor_ingredient, dish_format, cuisine}.
    signature_json: Mapped[dict | list | None] = mapped_column(JSONB)
    # Market pick (Prompt 28 A): a recipe anchored on a current deal the user
    # does NOT own — the intentional purchase. market_anchor:
    # {name, ingredient_id, sale_price, price_unit, savings_pct, store}.
    is_market_pick: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    market_anchor_json: Mapped[dict | None] = mapped_column(JSONB)
    # Honesty flags a recipe ships with (Prompt 32 C): e.g.
    # {"protein_below_floor": {"protein_g": 35, "floor_g": 54},
    #  "heavy": {"calories": 1104, "cap": 1100, "daily_target": 2000}}.
    # NULL/absent = clean. Rendered as visible amber chips on card + detail.
    quality_flags_json: Mapped[dict | None] = mapped_column(JSONB)
    # rating: -1 thumbs down, 1 thumbs up, null unrated
    rating: Mapped[int | None] = mapped_column(SmallInteger)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WeekRecipe(Base):
    __tablename__ = "week_recipes"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "recipe_id", "week_start", name="uq_week_recipe"
        ),
        Index("ix_week_recipes_user_week", "user_id", "week_start"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"), nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    is_cooked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    cooked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
