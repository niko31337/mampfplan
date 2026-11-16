from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import now
from app.db import Base

KIND_RECIPE = "recipe"
KIND_NOTE = "note"


class Space(Base):
    """A household. Access only via the secret token; all data belongs to exactly one household."""

    __tablename__ = "spaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    # admin household: may create further households (the one created on first start)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=now)


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(ForeignKey("spaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(50))


class Recipe(Base):
    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(ForeignKey("spaces.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    servings: Mapped[int | None] = mapped_column(Integer)
    image_url: Mapped[str | None] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(20), default="manual")
    source_url: Mapped[str | None] = mapped_column(String(500))
    steps: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)  # soft delete (undo, statistics)

    ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        order_by="RecipeIngredient.position", cascade="all, delete-orphan"
    )


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(100))
    amount: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20), default="")


class PlanEntry(Base):
    __tablename__ = "plan_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(ForeignKey("spaces.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(10))
    recipe_id: Mapped[int | None] = mapped_column(ForeignKey("recipes.id"))
    note: Mapped[str | None] = mapped_column(String(300))
    cooked: Mapped[bool] = mapped_column(Boolean, default=False)

    recipe: Mapped[Recipe | None] = relationship()


class Shop(Base):
    """A shopping list of the household, usually one per store (Lidl, Aldi, dm, ...)."""

    __tablename__ = "shops"

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(ForeignKey("spaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(50))
    # preset key (logo), "custom" for a freely named list, "default" for the automatically created one
    kind: Mapped[str] = mapped_column(String(20), default="custom")
    position: Mapped[int] = mapped_column(Integer, default=0)


class ShoppingItem(Base):
    __tablename__ = "shopping_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(ForeignKey("spaces.id"), index=True)
    shop_id: Mapped[int | None] = mapped_column(ForeignKey("shops.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    amount: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20), default="")
    category: Mapped[str] = mapped_column(String(40), default="Sonstiges")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    checked: Mapped[bool] = mapped_column(Boolean, default=False)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    added_by: Mapped[str | None] = mapped_column(String(50))  # profile name: added the item
    checked_by: Mapped[str | None] = mapped_column(String(50))  # profile name: put it in the cart
    recipe_id: Mapped[int | None] = mapped_column(ForeignKey("recipes.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CategoryOrder(Base):
    """Custom order of the categories on a shopping list (e.g. by the route through the supermarket)."""

    __tablename__ = "category_order"

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(ForeignKey("spaces.id"), index=True)
    shop_id: Mapped[int | None] = mapped_column(ForeignKey("shops.id"), index=True)
    category: Mapped[str] = mapped_column(String(40))
    position: Mapped[int] = mapped_column(Integer, default=0)


class CategoryOverride(Base):
    """Category of an item as corrected by the household, learned when it is moved to another category."""

    __tablename__ = "category_overrides"
    __table_args__ = (UniqueConstraint("space_id", "name_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(ForeignKey("spaces.id"), index=True)
    name_key: Mapped[str] = mapped_column(String(100))  # cleaned-up, lowercase item name
    category: Mapped[str] = mapped_column(String(40))


class CatalogItem(Base):
    """Predefined items (groceries, household, toiletries) for the shopping list."""

    __tablename__ = "catalog_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    category: Mapped[str] = mapped_column(String(40), index=True)


class CatalogName(Base):
    """Translated name of a catalog item (en/fr/es); the German name is in CatalogItem."""

    __tablename__ = "catalog_names"
    __table_args__ = (UniqueConstraint("catalog_item_id", "lang"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    catalog_item_id: Mapped[int] = mapped_column(ForeignKey("catalog_items.id"), index=True)
    lang: Mapped[str] = mapped_column(String(5), index=True)
    name: Mapped[str] = mapped_column(String(100))


class ActivityLog(Base):
    """Change history; `undo_ops` holds the inverse operations (see services/history.py)."""

    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(ForeignKey("spaces.id"), index=True)
    member_name: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    undo_ops: Mapped[str] = mapped_column(Text, default="[]")
    undone_at: Mapped[datetime | None] = mapped_column(DateTime)
