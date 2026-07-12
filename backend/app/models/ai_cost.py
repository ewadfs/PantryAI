from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AICostEvent(Base):
    """One Claude API call's usage + computed cost (Prompt 27 observability).

    Tagged with a ``category`` (generation, pre-generation, scan, circular,
    critic) and linked to its recipe batch (``batch_at``) or ``circular_fetch_id``
    so /stats/ai-costs can slice spend by category and time window.
    """

    __tablename__ = "ai_cost_events"
    __table_args__ = (
        Index("ix_ai_cost_events_created", "created_at"),
        Index("ix_ai_cost_events_category_created", "category", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    # Pipeline stage within a category (Prompt 32 A1): concepts | critic |
    # concept_fix | details | detail_fix | extraction. Lets the ledger answer
    # "which model actually served Stage 1?" without inference.
    stage: Mapped[str | None] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    cache_read_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    cache_write_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    batch_api: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    # Recipe batch this call belongs to (generation/critic/pre-generation).
    batch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Circular fetch this call belongs to (circular extraction).
    circular_fetch_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
