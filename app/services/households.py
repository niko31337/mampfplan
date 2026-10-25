"""Haushalte (Mandanten) und ihre Profile.

- Jeder Haushalt hat einen eigenen geheimen Link; alle Daten hängen an genau einem Haushalt.
- Der Verwaltungs-Haushalt (`is_admin`) darf weitere Haushalte anlegen.
- In jedem Haushalt darf das erste Profil (das älteste) weitere Profile anlegen.

Hinweis: Ohne Login ist das eine Bedienungs-, keine Sicherheitsgrenze – wer den Link hat, kann jedes
Profil des Haushalts wählen. Die eigentliche Grenze zwischen Haushalten ist der geheime Link.
"""

import secrets
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.models import (
    ActivityLog,
    CategoryOrder,
    Member,
    PlanEntry,
    Recipe,
    RecipeIngredient,
    ShoppingItem,
    Space,
)

MAX_NAME_LENGTH = 50
MAX_HOUSEHOLD_NAME_LENGTH = 100
MAX_MEMBERS_PER_HOUSEHOLD = 20
MAX_HOUSEHOLDS = 200
BADGE_COLORS = 6


class HouseholdError(ValueError):
    """Ungültige Eingabe (Meldung ist für Benutzer gedacht)."""


def new_token() -> str:
    return secrets.token_urlsafe(32)


def members_of(db: Session, space_id: int) -> list[Member]:
    return list(db.scalars(select(Member).where(Member.space_id == space_id).order_by(Member.id)))


def first_member_id(db: Session, space_id: int) -> int | None:
    return db.scalar(select(func.min(Member.id)).where(Member.space_id == space_id))


def is_owner(db: Session, member: Member) -> bool:
    """Das erste (älteste) Profil eines Haushalts verwaltet dessen Profile."""
    return first_member_id(db, member.space_id) == member.id


def _clean(value: str, max_length: int, label: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise HouseholdError(f"{label} fehlt.")
    if len(value) > max_length:
        raise HouseholdError(f"{label} ist zu lang (höchstens {max_length} Zeichen).")
    return value


def add_member(db: Session, space: Space, name: str) -> Member:
    name = _clean(name, MAX_NAME_LENGTH, "Name")
    existing = members_of(db, space.id)
    if any(m.name.casefold() == name.casefold() for m in existing):
        raise HouseholdError(f"Ein Profil „{name}“ gibt es in diesem Haushalt schon.")
    if len(existing) >= MAX_MEMBERS_PER_HOUSEHOLD:
        raise HouseholdError(f"Höchstens {MAX_MEMBERS_PER_HOUSEHOLD} Profile pro Haushalt.")
    member = Member(space_id=space.id, name=name)
    db.add(member)
    db.commit()
    return member


def rename_member(db: Session, member: Member, new_name: str) -> None:
    """Profil umbenennen. Gespeicherte Namen (Kürzel, Verlauf, Rezept-Autor) ziehen mit – nur in diesem Haushalt."""
    new_name = _clean(new_name, MAX_NAME_LENGTH, "Name")
    if new_name == member.name:
        return
    others = [m for m in members_of(db, member.space_id) if m.id != member.id]
    if any(m.name.casefold() == new_name.casefold() for m in others):
        raise HouseholdError(f"Ein Profil „{new_name}“ gibt es in diesem Haushalt schon.")
    old_name, space_id = member.name, member.space_id
    member.name = new_name
    for column in (ShoppingItem.added_by, ShoppingItem.checked_by):
        db.execute(
            update(ShoppingItem)
            .where(ShoppingItem.space_id == space_id, column == old_name)
            .values({column.key: new_name})
        )
    db.execute(update(Recipe).where(Recipe.space_id == space_id, Recipe.created_by == old_name).values(created_by=new_name))
    db.execute(
        update(ActivityLog)
        .where(ActivityLog.space_id == space_id, ActivityLog.member_name == old_name)
        .values(member_name=new_name)
    )
    db.commit()


def delete_member(db: Session, member: Member, acting: Member) -> None:
    """Profil löschen. Das eigene Profil nicht – so bleibt immer mindestens eines (und die Verwaltung) übrig.

    Bereits Eingetragenes bleibt erhalten und zeigt weiter den alten Namen.
    """
    if member.id == acting.id:
        raise HouseholdError("Das eigene Profil kannst du nicht löschen.")
    db.delete(member)
    db.commit()


def delete_household(db: Session, space: Space) -> None:
    """Haushalt mit allen Daten endgültig löschen (in Fremdschlüssel-Reihenfolge)."""
    if space.is_admin:
        raise HouseholdError("Der Verwaltungs-Haushalt kann nicht gelöscht werden.")
    recipe_ids = select(Recipe.id).where(Recipe.space_id == space.id)
    for statement in (
        delete(ActivityLog).where(ActivityLog.space_id == space.id),
        delete(PlanEntry).where(PlanEntry.space_id == space.id),
        delete(ShoppingItem).where(ShoppingItem.space_id == space.id),
        delete(CategoryOrder).where(CategoryOrder.space_id == space.id),
        delete(RecipeIngredient).where(RecipeIngredient.recipe_id.in_(recipe_ids)),
        delete(Recipe).where(Recipe.space_id == space.id),
        delete(Member).where(Member.space_id == space.id),
        delete(Space).where(Space.id == space.id),
    ):
        db.execute(statement.execution_options(synchronize_session=False))
    db.commit()


def create_household(db: Session, name: str, owner_name: str) -> Space:
    """Neuer Haushalt mit eigenem Link und dem ersten Profil (das weitere Profile anlegen darf)."""
    name = _clean(name, MAX_HOUSEHOLD_NAME_LENGTH, "Name des Haushalts")
    owner_name = _clean(owner_name, MAX_NAME_LENGTH, "Name des ersten Profils")
    if (db.scalar(select(func.count(Space.id))) or 0) >= MAX_HOUSEHOLDS:
        raise HouseholdError(f"Höchstens {MAX_HOUSEHOLDS} Haushalte.")
    space = Space(token=new_token(), name=name, is_admin=False)
    db.add(space)
    db.flush()
    db.add(Member(space_id=space.id, name=owner_name))
    db.commit()
    return space


def rotate_token(db: Session, space: Space) -> None:
    """Neuer Link; der alte ist sofort ungültig (Daten bleiben erhalten)."""
    space.token = new_token()
    db.commit()


@dataclass
class HouseholdInfo:
    space: Space
    members: list[str]


def list_households(db: Session) -> list[HouseholdInfo]:
    spaces = db.scalars(select(Space).order_by(Space.id)).all()
    return [HouseholdInfo(space, [m.name for m in members_of(db, space.id)]) for space in spaces]


@dataclass(frozen=True)
class Badge:
    text: str
    color: int


def badges(member_names: list[str]) -> dict[str, Badge]:
    """Kürzel je Profil: Anfangsbuchstabe; bei gleichem Anfangsbuchstaben die ersten zwei Buchstaben.

    Die Farbe richtet sich nach der Reihenfolge der Profile, damit sich Kürzel auch farblich unterscheiden.
    """
    result: dict[str, Badge] = {}
    for index, name in enumerate(member_names):
        first = name[:1].upper()
        clash = any(other != name and other[:1].upper() == first for other in member_names)
        text = (name[:1].upper() + name[1:2].lower()) if clash else first
        result[name] = Badge(text or "?", index % BADGE_COLORS)
    return result


def badge_for(name: str | None, known: dict[str, Badge]) -> Badge | None:
    """Kürzel für einen gespeicherten Namen – auch für Profile, die es nicht (mehr) gibt."""
    if not name:
        return None
    return known.get(name) or Badge(name[:1].upper() or "?", BADGE_COLORS - 1)
