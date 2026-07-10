from sqlalchemy import Boolean, Integer, String, Text, text
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
