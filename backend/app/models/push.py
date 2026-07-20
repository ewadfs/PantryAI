from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PushSubscription(Base):
    """A browser's Web Push subscription (P41 A). One row per endpoint;
    a user may hold several (phone + laptop). Deleted on unsubscribe and
    on 404/410 from the push service (server-honored opt-out)."""

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    endpoint: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PushSend(Base):
    """Log of flyer-flip notifications actually sent (P41 A4 caps).

    One row per (user, fetch): "once per flyer flip" is a unique-row check,
    "max 2/week" is a count over sent_at. No streaks, no re-engagement —
    this table exists to LIMIT sending, not to drive it."""

    __tablename__ = "push_sends"
    __table_args__ = (
        Index("ix_push_sends_user_sent", "user_id", "sent_at"),
        Index(
            "uq_push_sends_user_fetch", "user_id", "fetch_id", unique=True
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    fetch_id: Mapped[int] = mapped_column(
        ForeignKey("circular_fetches.id"), nullable=False
    )
    chain_id: Mapped[int] = mapped_column(Integer, nullable=False)
    region_key: Mapped[str | None] = mapped_column(String(120))
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
