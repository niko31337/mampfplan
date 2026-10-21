import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CatalogItem, CategoryOrder, RecipeIngredient, ShoppingItem
from app.services.history import Action

DEFAULT_CATEGORY = "Sonstiges"
CATEGORY_ORDER = [
    "Obst & Gemüse",
    "Brot & Backwaren",
    "Milch, Käse & Eier",
    "Fleisch, Fisch & Wurst",
    "Tiefkühl",
    "Vorrat & Konserven",
    "Gewürze, Öle & Soßen",
    "Süßes & Snacks",
    "Getränke",
    "Haushalt & Reinigung",
    "Hygiene & Drogerie",
    "Baby & Haustier",
    DEFAULT_CATEGORY,
]

def category_rank(category: str) -> int:
    """Rang in der Standardreihenfolge (für den Artikelkatalog)."""
    return CATEGORY_ORDER.index(category) if category in CATEGORY_ORDER else len(CATEGORY_ORDER)


_PLURAL_MARKER = re.compile(r"(?<=\w)\((?:n|en|e|s|er|r|nen)\)")  # "Karotte(n)", "Stück(e)"
_PARENTHESES = re.compile(r"\([^)]*\)")
_PLURAL_ENDINGS = ("en", "er", "n", "e", "s")
_FOLD = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "s"})


def clean_name(name: str) -> str:
    """"Karotte(n)" -> "Karotte", "Zwiebel(n)" -> "Zwiebel"; sonstige Klammern bleiben erhalten."""
    return re.sub(r"\s+", " ", _PLURAL_MARKER.sub("", name)).strip()


def _stem(word: str) -> str:
    word = word.casefold().translate(_FOLD)
    for ending in _PLURAL_ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 4:
            return word[: -len(ending)]
    return word


def _stem_key(name: str) -> str:
    """Vergleichsschlüssel: ohne Klammern, Singular/Plural und Umlaute vereinheitlicht."""
    words = re.findall(r"[^\W\d_]+", _PARENTHESES.sub(" ", _PLURAL_MARKER.sub("", name)))
    return " ".join(_stem(w) for w in words)


def guess_category(db: Session, name: str) -> str:
    """Kategorie aus dem Katalog ableiten: erst gleicher Stamm, sonst längster Teiltreffer."""
    needle = _stem_key(name)
    if not needle:
        return DEFAULT_CATEGORY
    best: tuple[int, str] | None = None
    for item in db.scalars(select(CatalogItem).order_by(CatalogItem.id)):
        known = _stem_key(item.name)
        if not known:
            continue
        if known == needle:
            return item.category
        if len(known) >= 4 and len(needle) >= 4 and (known in needle or needle in known):
            if best is None or len(known) > best[0]:
                best = (len(known), item.category)
    return best[1] if best else DEFAULT_CATEGORY


def add_item(
    db: Session,
    act: Action,
    space_id: int,
    name: str,
    amount: float | None = None,
    unit: str = "",
    category: str | None = None,
    recipe_id: int | None = None,
) -> tuple[ShoppingItem, bool]:
    """Fügt einen Artikel hinzu oder führt ihn mit einem offenen gleichnamigen zusammen.

    Rückgabe: (Artikel, zusammengeführt?)
    """
    name = clean_name(name)[:100]
    unit = unit.strip()[:20]
    existing = db.scalars(
        select(ShoppingItem).where(ShoppingItem.space_id == space_id, ShoppingItem.checked.is_(False))
    ).all()
    for item in existing:
        if item.name.casefold() != name.casefold() or item.unit.casefold() != unit.casefold():
            continue
        if item.amount is None and amount is None:
            return item, True  # steht schon auf der Liste
        if item.amount is not None and amount is not None:
            act.updating(item)
            item.amount += amount
            return item, True

    last_position = db.scalar(select(func.max(ShoppingItem.position)).where(ShoppingItem.space_id == space_id))
    item = ShoppingItem(
        space_id=space_id,
        name=name,
        amount=amount,
        unit=unit,
        category=category or guess_category(db, name),
        position=0 if last_position is None else last_position + 1,
        recipe_id=recipe_id,
    )
    db.add(item)
    db.flush()
    act.created(item)
    return item, False


def add_recipe_ingredients(
    db: Session, act: Action, space_id: int, recipe_id: int, ingredients: list[RecipeIngredient]
) -> int:
    for ing in ingredients:
        add_item(db, act, space_id, ing.name, ing.amount, ing.unit, recipe_id=recipe_id)
    return len(ingredients)


def search_catalog(db: Session, query: str, limit: int = 12) -> list[CatalogItem]:
    pattern = "%" + query.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
    return list(
        db.scalars(
            select(CatalogItem).where(CatalogItem.name.ilike(pattern, escape="\\")).order_by(CatalogItem.name).limit(limit)
        )
    )


# --- Reihenfolge -----------------------------------------------------------------------------


def category_order(db: Session, space_id: int) -> list[str]:
    """Kategorien in der eingestellten Reihenfolge (nicht gespeicherte hängen in Standardreihenfolge an)."""
    saved = list(
        db.scalars(
            select(CategoryOrder.category).where(CategoryOrder.space_id == space_id).order_by(CategoryOrder.position)
        )
    )
    return saved + [c for c in CATEGORY_ORDER if c not in saved]


def item_sort_key(item: ShoppingItem) -> tuple[int, str, int]:
    return item.position, item.name.casefold(), item.id


def reorder_items(db: Session, act: Action, space_id: int, category: str, ordered_ids: list[int]) -> bool:
    """Setzt die Reihenfolge der offenen Artikel einer Kategorie.

    Artikel, die in `ordered_ids` fehlen (z. B. zwischenzeitlich von jemand anderem ergänzt),
    bleiben in ihrer bisherigen Reihenfolge am Ende. Unbekannte IDs werden ignoriert.
    Rückgabe: wurde etwas geändert?
    """
    current = sorted(
        db.scalars(
            select(ShoppingItem).where(
                ShoppingItem.space_id == space_id,
                ShoppingItem.checked.is_(False),
                ShoppingItem.category == category,
            )
        ),
        key=item_sort_key,
    )
    by_id = {item.id: item for item in current}
    wanted = [by_id[i] for i in dict.fromkeys(ordered_ids) if i in by_id]
    ordered = wanted + [item for item in current if item not in wanted]
    changed = False
    for position, item in enumerate(ordered):
        if item.position != position:
            act.updating(item)
            item.position = position
            changed = True
    return changed


def set_category_order(db: Session, act: Action, space_id: int, ordered_visible: list[str]) -> bool:
    """Ordnet die übergebenen (sichtbaren) Kategorien neu, ohne die Plätze der übrigen zu verändern."""
    full = category_order(db, space_id)
    wanted = [c for c in dict.fromkeys(ordered_visible) if c in full]
    slots = [i for i, c in enumerate(full) if c in wanted]
    new_full = list(full)
    for slot, category in zip(slots, wanted, strict=True):
        new_full[slot] = category
    if new_full == full:
        return False
    full = new_full

    rows = {
        row.category: row
        for row in db.scalars(select(CategoryOrder).where(CategoryOrder.space_id == space_id))
    }
    for position, name in enumerate(full):
        row = rows.get(name)
        if row is None:
            row = CategoryOrder(space_id=space_id, category=name, position=position)
            db.add(row)
            db.flush()
            act.created(row)
        elif row.position != position:
            act.updating(row)
            row.position = position
    return True
