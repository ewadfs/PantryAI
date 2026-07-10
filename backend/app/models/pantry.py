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
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PantryScan(Base):
    __tablename__ = "pantry_scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    image_keys: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    ai_response_json: Mapped[dict | list | None] = mapped_column(JSONB)
    items_detected: Mapped[int | None] = mapped_column(Integer)
    items_confirmed: Mapped[int | None] = mapped_column(Integer)
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PantryItem(Base):
    __tablename__ = "pantry_items"
    __table_args__ = (
        Index("ix_pantry_items_user_active", "user_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    ingredient_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingredient_master.id")
    )
    name: Mapped[str | None] = mapped_column(String(200))
    quantity_estimate: Mapped[str | None] = mapped_column(String(50))
    unit: Mapped[str | None] = mapped_column(String(30))
    category: Mapped[str | None] = mapped_column(String(50))
    brand: Mapped[str | None] = mapped_column(String(100))
    freshness: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'good'")
    )
    estimated_expiry: Mapped[date | None] = mapped_column(Date)
    is_staple: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    source: Mapped[str | None] = mapped_column(String(20))
    scan_id: Mapped[int | None] = mapped_column(ForeignKey("pantry_scans.id"))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
