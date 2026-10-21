from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import now
from app.db import Base

KIND_RECIPE = "recipe"
KIND_NOTE = "note"


class Space(Base):
    """Ein geteilter Bereich (Familie). Zugriff nur über das geheime Token."""

    __tablename__ = "spaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))


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
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)  # Soft-Delete (Undo, Statistik)

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


class ShoppingItem(Base):
    __tablename__ = "shopping_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(ForeignKey("spaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    amount: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20), default="")
    category: Mapped[str] = mapped_column(String(40), default="Sonstiges")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    checked: Mapped[bool] = mapped_column(Boolean, default=False)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    recipe_id: Mapped[int | None] = mapped_column(ForeignKey("recipes.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CategoryOrder(Base):
    """Eigene Reihenfolge der Kategorien auf der Einkaufsliste (z. B. nach Supermarkt-Rundgang)."""

    __tablename__ = "category_order"

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(ForeignKey("spaces.id"), index=True)
    category: Mapped[str] = mapped_column(String(40))
    position: Mapped[int] = mapped_column(Integer, default=0)


class CatalogItem(Base):
    """Vordefinierte Artikel (Lebensmittel, Haushalt, Hygiene) für die Einkaufsliste."""

    __tablename__ = "catalog_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    category: Mapped[str] = mapped_column(String(40), index=True)


class ActivityLog(Base):
    """Änderungsverlauf; `undo_ops` enthält die Umkehr-Operationen (siehe services/history.py)."""

    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(ForeignKey("spaces.id"), index=True)
    member_name: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    undo_ops: Mapped[str] = mapped_column(Text, default="[]")
    undone_at: Mapped[datetime | None] = mapped_column(DateTime)
