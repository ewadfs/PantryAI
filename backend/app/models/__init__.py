"""SQLAlchemy ORM models.

Importing every model here ensures they are all registered on
``Base.metadata`` so Alembic autogenerate can discover them.
"""

from app.models.ai_cost import AICostEvent
from app.models.deal import CircularFetch, DealCache
from app.models.ingredient import IngredientMaster
from app.models.pantry import PantryItem, PantryScan
from app.models.recipe import Recipe, WeekRecipe
from app.models.shopping import ShoppingList, ShoppingListItem
from app.models.store import (
    StoreLocation,
    StoreRequest,
    SupportedChain,
    UserStore,
    ZipDiscoveryCache,
)
from app.models.user import User

__all__ = [
    "AICostEvent",
    "User",
    "SupportedChain",
    "StoreLocation",
    "UserStore",
    "StoreRequest",
    "ZipDiscoveryCache",
    "IngredientMaster",
    "PantryScan",
    "PantryItem",
    "CircularFetch",
    "DealCache",
    "Recipe",
    "WeekRecipe",
    "ShoppingList",
    "ShoppingListItem",
]
