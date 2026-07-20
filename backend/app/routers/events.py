"""Client-side event reporting (P40 C6). Server-side hooks log the rest."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.services import events
from app.services.auth import get_current_user

router = APIRouter(tags=["events"])


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: str = Field(max_length=40)
    meta: dict | None = None


@router.post("/events", status_code=status.HTTP_202_ACCEPTED)
async def report_event(
    payload: EventIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if payload.event not in events.CLIENT_EVENTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unknown client event.",
        )
    events.log(db, current_user.id, payload.event, **(payload.meta or {}))
    await db.flush()
    return {"status": "logged"}
