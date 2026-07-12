from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    """Application user. Profile fields are merged in for the MVP (one table)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    supabase_user_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    email: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(String(255))
    zip_code: Mapped[str | None] = mapped_column(String(10))

    goal: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=text("'eat_healthy'")
    )
    calorie_target: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("2000")
    )
    protein_target: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("100")
    )
    diet_type: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=text("'omnivore'")
    )
    allergies: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    cuisine_preferences: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    excluded_ingredients: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    skill_level: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'intermediate'")
    )
    max_prep_time: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("45")
    )
    household_size: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("4")
    )
    # Free-text taste profile injected verbatim into generation + the critic.
    taste_notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
