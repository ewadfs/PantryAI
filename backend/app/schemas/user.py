from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserRead(BaseModel):
    """Full user profile returned to the client."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    supabase_user_id: str
    email: str | None = None
    name: str | None = None
    zip_code: str | None = None
    goal: str
    calorie_target: int
    protein_target: int
    diet_type: str
    allergies: list[str]
    cuisine_preferences: list[str]
    excluded_ingredients: list[str]
    skill_level: str
    max_prep_time: int
    household_size: int
    taste_notes: str | None = None
    created_at: datetime
    updated_at: datetime


class UserUpdate(BaseModel):
    """Partial profile update. Only supplied fields are applied."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=255)
    zip_code: str | None = Field(default=None, max_length=10)
    goal: str | None = Field(default=None, max_length=30)
    calorie_target: int | None = Field(default=None, ge=0, le=20000)
    protein_target: int | None = Field(default=None, ge=0, le=2000)
    diet_type: str | None = Field(default=None, max_length=30)
    allergies: list[str] | None = None
    cuisine_preferences: list[str] | None = None
    excluded_ingredients: list[str] | None = None
    skill_level: str | None = Field(default=None, max_length=20)
    max_prep_time: int | None = Field(default=None, ge=0, le=1440)
    household_size: int | None = Field(default=None, ge=1, le=50)
    taste_notes: str | None = Field(default=None, max_length=2000)
