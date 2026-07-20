from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Event(Base):
    """Minimal product-event log (P40 C): our DB, our numbers.

    One row per funnel event — signup, store_selected, first_batch_viewed,
    recipe_opened, scan_started, scan_confirmed, taste_set, save_to_week,
    list_built, list_completed (+ P41: push_subscribed, push_opened,
    share_created, share_visited, share_converted). D1/D7 return is computed
    from timestamps, not stored. No third-party analytics.
    """

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_user_ts", "user_id", "ts"),
        Index("ix_events_event_ts", "event", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    event: Mapped[str] = mapped_column(String(40), nullable=False)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    meta: Mapped[dict | None] = mapped_column(JSONB)
