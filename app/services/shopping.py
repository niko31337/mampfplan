import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.i18n import DEFAULT_LANGUAGE, get_language
from app.models import CatalogItem, CatalogName, CategoryOrder, CategoryOverride, RecipeIngredient, ShoppingItem
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
    """Rank in the default order (for the item catalog)."""
    return CATEGORY_ORDER.index(category) if category in CATEGORY_ORDER else len(CATEGORY_ORDER)


_PLURAL_MARKER = re.compile(r"(?<=\w)\((?:n|en|e|s|er|r|nen)\)")  # "Karotte(n)", "Stück(e)"
_PARENTHESES = re.compile(r"\([^)]*\)")
_PLURAL_ENDINGS = ("en", "er", "n", "e", "s")
_FOLD = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "s"})


def clean_name(name: str) -> str:
    """"Karotte(n)" -> "Karotte", "Zwiebel(n)" -> "Zwiebel"; other parentheses are kept."""
    return re.sub(r"\s+", " ", _PLURAL_MARKER.sub("", name)).strip()


def _stem(word: str) -> str:
    word = word.casefold().translate(_FOLD)
    for ending in _PLURAL_ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 4:
            return word[: -len(ending)]
    return word


def _stem_key(name: str) -> str:
    """Comparison key: without parentheses, singular/plural and umlauts normalized."""
    words = re.findall(r"[^\W\d_]+", _PARENTHESES.sub(" ", _PLURAL_MARKER.sub("", name)))
    return " ".join(_stem(w) for w in words)


def guess_category(db: Session, name: str) -> str:
    """Derive the category from the catalog: same stem first, otherwise the longest partial match."""
    needle = _stem_key(name)
    if not needle:
        return DEFAULT_CATEGORY
    best: tuple[int, str] | None = None
    # names in all languages: „Milk“, „Lait“ and „Leche“ end up in dairy just like „Milch“
    names = db.execute(select(CatalogItem.name, CatalogItem.category).order_by(CatalogItem.id)).all()
    names += db.execute(
        select(CatalogName.name, CatalogItem.category).join(CatalogItem, CatalogItem.id == CatalogName.catalog_item_id)
    ).all()
    for name, category in names:
        known = _stem_key(name)
        if not known:
            continue
        if known == needle:
            return category
        if len(known) >= 4 and len(needle) >= 4 and (known in needle or needle in known):
            if best is None or len(known) > best[0]:
                best = (len(known), category)
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
    added_by: str | None = None,
    *,
    shop_id: int,
) -> tuple[ShoppingItem, bool]:
    """Adds an item to a list (shop) or merges it with an open item of the same name there.

    When merging, it keeps track of who originally added the item.
    Returns: (item, merged?)
    """
    name = clean_name(name)[:100]
    unit = unit.strip()[:20]
    item = _mergeable(db, space_id, shop_id, name, amount, unit)
    if item is not None:
        if amount is not None:
            act.updating(item)
            item.amount += amount
        return item, True  # already on the list

    last_position = db.scalar(select(func.max(ShoppingItem.position)).where(ShoppingItem.space_id == space_id))
    item = ShoppingItem(
        space_id=space_id,
        shop_id=shop_id,
        name=name,
        amount=amount,
        unit=unit,
        # the household's own correction beats the catalog suggestion and automatic detection
        category=learned_category(db, space_id, name) or category or guess_category(db, name),
        position=0 if last_position is None else last_position + 1,
        recipe_id=recipe_id,
        added_by=added_by,
    )
    db.add(item)
    db.flush()
    act.created(item)
    return item, False


def _mergeable(
    db: Session, space_id: int, shop_id: int, name: str, amount: float | None, unit: str, exclude_id: int | None = None
) -> ShoppingItem | None:
    """Open item on the same list that a new item with this name/amount can be merged into."""
    existing = db.scalars(
        select(ShoppingItem).where(
            ShoppingItem.space_id == space_id, ShoppingItem.shop_id == shop_id, ShoppingItem.checked.is_(False)
        )
    ).all()
    for item in existing:
        if item.id == exclude_id:
            continue
        if item.name.casefold() != name.casefold() or item.unit.casefold() != unit.casefold():
            continue
        if (item.amount is None) == (amount is None):
            return item
    return None


def add_recipe_ingredients(
    db: Session,
    act: Action,
    space_id: int,
    recipe_id: int,
    ingredients: list[RecipeIngredient],
    added_by: str | None = None,
    *,
    shop_id: int,
) -> int:
    for ing in ingredients:
        add_item(db, act, space_id, ing.name, ing.amount, ing.unit, recipe_id=recipe_id, added_by=added_by, shop_id=shop_id)
    return len(ingredients)


@dataclass(frozen=True)
class CatalogEntry:
    """Catalog item in the display language; `category` stays the internal (German) key."""

    name: str
    category: str


def _localized(db: Session, lang: str):
    if lang == DEFAULT_LANGUAGE:
        return select(CatalogItem.name, CatalogItem.category)
    return select(CatalogName.name, CatalogItem.category).join(CatalogItem, CatalogItem.id == CatalogName.catalog_item_id).where(
        CatalogName.lang == lang
    )


def catalog_entries(db: Session, lang: str | None = None) -> list[CatalogEntry]:
    """The whole catalog in the display language (German where translations are missing)."""
    lang = lang or get_language()
    rows = db.execute(_localized(db, lang)).all() or db.execute(_localized(db, DEFAULT_LANGUAGE)).all()
    return [CatalogEntry(name, category) for name, category in rows]


def search_catalog(db: Session, query: str, limit: int = 12, lang: str | None = None) -> list[CatalogEntry]:
    lang = lang or get_language()
    pattern = "%" + query.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
    stmt = _localized(db, lang)
    column = CatalogItem.name if lang == DEFAULT_LANGUAGE else CatalogName.name
    rows = db.execute(stmt.where(column.ilike(pattern, escape="\\")).order_by(column).limit(limit)).all()
    return [CatalogEntry(name, category) for name, category in rows]


# --- Order -----------------------------------------------------------------------------------


def _shop_or_default(db: Session, space_id: int, shop_id: int | None) -> int | None:
    from app.services.shops import default_shop_id

    return shop_id if shop_id is not None else default_shop_id(db, space_id)


def _shop_filter(column, shop_id: int | None):
    return column.is_(None) if shop_id is None else column == shop_id


def category_order(db: Session, space_id: int, shop_id: int | None = None) -> list[str]:
    """Categories of a list (shop) in the configured order (unsaved ones are appended in the default order).

    Without `shop_id`: the household's first list.
    """
    shop_id = _shop_or_default(db, space_id, shop_id)
    saved = list(
        db.scalars(
            select(CategoryOrder.category)
            .where(CategoryOrder.space_id == space_id, _shop_filter(CategoryOrder.shop_id, shop_id))
            .order_by(CategoryOrder.position)
        )
    )
    saved = list(dict.fromkeys(c for c in saved if c in CATEGORY_ORDER))
    return saved + [c for c in CATEGORY_ORDER if c not in saved]


def item_sort_key(item: ShoppingItem) -> tuple[int, str, int]:
    return item.position, item.name.casefold(), item.id


def reorder_items(
    db: Session, act: Action, space_id: int, shop_id: int, category: str, ordered_ids: list[int]
) -> bool:
    """Sets the order of the open items of a category on a list (shop).

    Items missing from `ordered_ids` (e.g. added by someone else in the meantime)
    keep their previous order at the end. Unknown IDs are ignored.
    Returns: was anything changed?
    """
    current = sorted(
        db.scalars(
            select(ShoppingItem).where(
                ShoppingItem.space_id == space_id,
                ShoppingItem.shop_id == shop_id,
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


def _name_key(name: str) -> str:
    return clean_name(name).casefold()[:100]


def learned_category(db: Session, space_id: int, name: str) -> str | None:
    """Category this household previously corrected for this item name."""
    return db.scalar(
        select(CategoryOverride.category).where(
            CategoryOverride.space_id == space_id, CategoryOverride.name_key == _name_key(name)
        )
    )


def remember_category(db: Session, act: Action, space_id: int, name: str, category: str) -> None:
    key = _name_key(name)
    existing = db.scalars(
        select(CategoryOverride).where(CategoryOverride.space_id == space_id, CategoryOverride.name_key == key)
    ).first()
    if existing is None:
        override = CategoryOverride(space_id=space_id, name_key=key, category=category)
        db.add(override)
        db.flush()
        act.created(override)
    elif existing.category != category:
        act.updating(existing)
        existing.category = category


def move_to_category(
    db: Session,
    act: Action,
    space_id: int,
    item: ShoppingItem,
    category: str,
    ordered_ids: list[int],
    shop_id: int | None = None,
) -> None:
    """Move an item into another category (optionally on another list), to the desired position there,
    and remember the category correction."""
    shop_id = shop_id or item.shop_id
    if item.category != category or item.shop_id != shop_id:
        act.updating(item)
        if item.category != category:
            item.category = category
            remember_category(db, act, space_id, item.name, category)
        item.shop_id = shop_id
        db.flush()
    reorder_items(db, act, space_id, shop_id, category, ordered_ids)


def move_to_shop(db: Session, act: Action, space_id: int, item: ShoppingItem, shop_id: int) -> ShoppingItem:
    """Move an item to another list. It keeps its category, so it lands in the right place there.

    If the same item is already open on the target list, the two are merged.
    Returns: the item on the target list.
    """
    if item.shop_id == shop_id:
        return item
    if not item.checked:
        twin = _mergeable(db, space_id, shop_id, item.name, item.amount, item.unit, exclude_id=item.id)
        if twin is not None:
            if item.amount is not None:
                act.updating(twin)
                twin.amount += item.amount
            act.deleting(item)
            return twin
    last_position = db.scalar(
        select(func.max(ShoppingItem.position)).where(
            ShoppingItem.space_id == space_id, ShoppingItem.shop_id == shop_id, ShoppingItem.category == item.category
        )
    )
    act.updating(item)
    item.shop_id = shop_id
    item.position = 0 if last_position is None else last_position + 1
    return item


def set_category_order(db: Session, act: Action, space_id: int, shop_id: int, ordered_visible: list[str]) -> bool:
    """Reorders the given categories without changing the positions of the others.

    With all categories (page „Store order“) this is exactly the desired overall order.
    """
    full = category_order(db, space_id, shop_id)
    wanted = [c for c in dict.fromkeys(ordered_visible) if c in full]
    slots = [i for i, c in enumerate(full) if c in wanted]
    new_full = list(full)
    for slot, category in zip(slots, wanted, strict=True):
        new_full[slot] = category
    return _save_category_order(db, act, space_id, shop_id, full, new_full)


def move_category(
    db: Session,
    act: Action,
    space_id: int,
    shop_id: int,
    category: str,
    before: str | None = None,
    after: str | None = None,
) -> bool:
    """Insert a category directly before `before` (or after `after`); the others keep their order.

    That way a category moved to the front stays at the front, even if other categories
    become visible later (the supermarket's order).
    """
    full = category_order(db, space_id, shop_id)
    if category not in full or category in (before, after):
        return False
    new_full = [c for c in full if c != category]
    if before in new_full:
        new_full.insert(new_full.index(before), category)
    elif after in new_full:
        new_full.insert(new_full.index(after) + 1, category)
    else:
        return False
    return _save_category_order(db, act, space_id, shop_id, full, new_full)


def _save_category_order(
    db: Session, act: Action, space_id: int, shop_id: int, old: list[str], full: list[str]
) -> bool:
    if full == old:
        return False
    rows = {
        row.category: row
        for row in db.scalars(
            select(CategoryOrder).where(CategoryOrder.space_id == space_id, CategoryOrder.shop_id == shop_id)
        )
    }
    for position, name in enumerate(full):
        row = rows.get(name)
        if row is None:
            row = CategoryOrder(space_id=space_id, shop_id=shop_id, category=name, position=position)
            db.add(row)
            db.flush()
            act.created(row)
        elif row.position != position:
            act.updating(row)
            row.position = position
    return True
