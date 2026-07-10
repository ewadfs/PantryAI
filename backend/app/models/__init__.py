"""SQLAlchemy ORM models.

Importing every model here ensures they are all registered on
``Base.metadata`` so Alembic autogenerate can discover them.
"""

from app.models.deal import CircularFetch, DealCache
from app.models.ingredient import IngredientMaster
from app.models.pantry import PantryItem, PantryScan
from app.models.recipe import Recipe, WeekRecipe
from app.models.shopping import ShoppingList, ShoppingListItem
from app.models.store import StoreLocation, SupportedChain, UserStore
from app.models.user import User

__all__ = [
    "User",
    "SupportedChain",
    "StoreLocation",
    "UserStore",
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
