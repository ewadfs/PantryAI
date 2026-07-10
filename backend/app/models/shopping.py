from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ShoppingList(Base):
    __tablename__ = "shopping_lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    week_start: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'active'")
    )
    total_known_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    deal_savings: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    item_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ShoppingListItem(Base):
    __tablename__ = "shopping_list_items"
    __table_args__ = (
        Index("ix_shopping_list_items_list_checked", "list_id", "is_checked"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    list_id: Mapped[int] = mapped_column(
        ForeignKey("shopping_lists.id"), nullable=False
    )
    ingredient_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingredient_master.id")
    )
    display_name: Mapped[str | None] = mapped_column(String(200))
    buy_quantity: Mapped[str | None] = mapped_column(String(50))
    unit: Mapped[str | None] = mapped_column(String(30))
    category: Mapped[str | None] = mapped_column(String(50))
    # price: null = unknown, NEVER fabricate
    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    is_on_sale: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    regular_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    deal_id: Mapped[int | None] = mapped_column(ForeignKey("deal_cache.id"))
    from_recipes: Mapped[dict | list | None] = mapped_column(JSONB)
    is_checked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_manual_add: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    notes: Mapped[str | None] = mapped_column(String(500))
