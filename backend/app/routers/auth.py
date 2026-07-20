from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.schemas.user import UserRead, UserUpdate
from app.services import events
from app.services.auth import get_current_user

router = APIRouter(tags=["auth"])

# Profile fields whose change means the user told us about their taste /
# household (P40 C: the "taste_set" funnel step).
_TASTE_FIELDS = {"taste_notes", "household_size", "protein_target", "max_prep_time"}


@router.get("/me", response_model=UserRead)
async def get_me(current_user: User = Depends(get_current_user)) -> User:
    """Return the current user's full profile (auto-creates on first sight)."""
    return current_user


@router.patch("/me", response_model=UserRead)
async def update_me(
    payload: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Partial update of the current user's profile."""
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(current_user, field, value)
    taste_changed = sorted(_TASTE_FIELDS & changes.keys())
    if taste_changed:
        events.log(db, current_user.id, "taste_set", fields=taste_changed)
    await db.flush()
    await db.refresh(current_user)
    return current_user
