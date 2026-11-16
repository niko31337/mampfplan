"""Several shopping lists per household – usually one per store (Lidl, Aldi, dm, ...).

Every household has at least one list. It is created automatically ("Einkaufsliste") and takes over
all items that don't belong to a list yet (data from before multiple lists existed). With only one
list everything ends up there; with several, the user picks the list when adding items.
"""

import re
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.i18n import _
from app.models import CategoryOrder, Shop, ShoppingItem
from app.services.history import Action

DEFAULT_KIND = "default"
CUSTOM_KIND = "custom"
DEFAULT_NAME = "Einkaufsliste"  # German source text, translated when displayed
MAX_SHOPS = 20
MAX_SHOP_NAME_LENGTH = 50


@dataclass(frozen=True)
class Preset:
    """A well-known store: name, website (for the favicon) and a fallback logo (colored tile with letters)."""

    key: str
    name: str
    domain: str
    color: str
    text: str
    text_color: str = "#ffffff"

    @property
    def logo_urls(self) -> list[str]:
        """Where the favicon is fetched from (on the server, never by the browser)."""
        return [f"https://www.{self.domain}/favicon.ico", f"https://www.{self.domain}/apple-touch-icon.png"]


PRESETS: dict[str, Preset] = {
    p.key: p
    for p in [
        Preset("lidl", "Lidl", "lidl.de", "#0050aa", "L", "#fff000"),
        Preset("aldi_sued", "Aldi Süd", "aldi-sued.de", "#00005f", "A"),
        Preset("aldi_nord", "Aldi Nord", "aldi-nord.de", "#1b9ad6", "A"),
        Preset("dm", "dm", "dm.de", "#002878", "dm", "#ffd700"),
        Preset("netto", "Netto", "netto-online.de", "#ffd400", "N", "#d40511"),
        Preset("edeka", "Edeka", "edeka.de", "#1c4c9c", "E", "#ffe500"),
        Preset("rewe", "Rewe", "rewe.de", "#cc071e", "R"),
        Preset("kaufland", "Kaufland", "kaufland.de", "#e10915", "K"),
        Preset("penny", "Penny", "penny.de", "#cd1414", "P", "#fff000"),
        Preset("rossmann", "Rossmann", "rossmann.de", "#c3002d", "R"),
    ]
}


class ShopError(Exception):
    """User error (shown as message)."""


class ShopRequired(ShopError):
    """Several lists exist, but none was chosen."""


def _clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip()


def shops_of(db: Session, space_id: int) -> list[Shop]:
    """All lists of the household in the configured order; creates the first one if needed."""
    shops = list(db.scalars(select(Shop).where(Shop.space_id == space_id).order_by(Shop.position, Shop.id)))
    changed = False
    if not shops:
        shop = Shop(space_id=space_id, name=DEFAULT_NAME, kind=DEFAULT_KIND, position=0)
        db.add(shop)
        db.flush()
        # store order saved before multiple lists existed belongs to the first list
        db.execute(
            update(CategoryOrder)
            .where(CategoryOrder.space_id == space_id, CategoryOrder.shop_id.is_(None))
            .values(shop_id=shop.id)
        )
        shops = [shop]
        changed = True
    # items without a list (older data, restored by undo) land on the first list
    orphans = db.execute(
        update(ShoppingItem)
        .where(ShoppingItem.space_id == space_id, ShoppingItem.shop_id.is_(None))
        .values(shop_id=shops[0].id)
    ).rowcount
    if changed or orphans:
        db.commit()
    return shops


def default_shop_id(db: Session, space_id: int) -> int | None:
    """The first list (without creating one)."""
    return db.scalar(select(Shop.id).where(Shop.space_id == space_id).order_by(Shop.position, Shop.id).limit(1))


def get_shop(db: Session, space_id: int, shop_id: int | None) -> Shop | None:
    shop = db.get(Shop, shop_id) if shop_id else None
    return shop if shop is not None and shop.space_id == space_id else None


def resolve_shop(db: Session, space_id: int, shop_id: int | None) -> Shop:
    """The list items should go to: the chosen one, or the only one. Raises ShopRequired otherwise."""
    shops = shops_of(db, space_id)
    if shop_id:
        for shop in shops:
            if shop.id == shop_id:
                return shop
        raise ShopError(_("Diese Einkaufsliste gibt es nicht (mehr)."))
    if len(shops) == 1:
        return shops[0]
    raise ShopRequired(_("Bitte wähle eine Einkaufsliste."))


def display_name(shop: Shop) -> str:
    if shop.kind == DEFAULT_KIND and shop.name == DEFAULT_NAME:
        return _(DEFAULT_NAME)
    return shop.name


def preset_of(shop: Shop) -> Preset | None:
    return PRESETS.get(shop.kind)


def initials(shop: Shop) -> str:
    """Letters for the logo tile of lists without a known store."""
    name = display_name(shop)
    return (name[:1] or "?").upper()


def _check_name(db: Session, space_id: int, name: str, exclude_id: int | None = None) -> str:
    name = _clean_name(name)
    if not name:
        raise ShopError(_("Bitte gib einen Namen für die Liste ein."))
    if len(name) > MAX_SHOP_NAME_LENGTH:
        raise ShopError(_("Der Name ist zu lang (höchstens %(max)s Zeichen).", max=MAX_SHOP_NAME_LENGTH))
    for shop in db.scalars(select(Shop).where(Shop.space_id == space_id)):
        if shop.id != exclude_id and display_name(shop).casefold() == name.casefold():
            raise ShopError(_("Eine Liste „%(name)s“ gibt es schon.", name=name))
    return name


def add_shop(db: Session, act: Action, space_id: int, kind: str, name: str = "") -> Shop:
    """New list for a known store (`kind` = preset key) or with its own name (`kind` = "custom")."""
    shops = shops_of(db, space_id)
    if len(shops) >= MAX_SHOPS:
        raise ShopError(_("Höchstens %(max)s Einkaufslisten möglich.", max=MAX_SHOPS))
    preset = PRESETS.get(kind)
    if preset is None:
        # a freely typed name of a known store still gets its logo
        typed = _clean_name(name).casefold()
        preset = next((p for p in PRESETS.values() if p.name.casefold() == typed), None)
        kind = preset.key if preset else CUSTOM_KIND
    name = _check_name(db, space_id, name or (preset.name if preset else ""))
    position = db.scalar(select(func.max(Shop.position)).where(Shop.space_id == space_id))
    shop = Shop(space_id=space_id, name=name, kind=kind, position=0 if position is None else position + 1)
    db.add(shop)
    db.flush()
    act.created(shop)
    return shop


def rename_shop(db: Session, act: Action, shop: Shop, name: str) -> bool:
    name = _check_name(db, shop.space_id, name, exclude_id=shop.id)
    if name == shop.name and shop.kind != DEFAULT_KIND:
        return False
    act.updating(shop)
    shop.name = name
    if shop.kind == DEFAULT_KIND:
        shop.kind = CUSTOM_KIND  # now has a name chosen by the household (no more translation)
    return True


def delete_shop(db: Session, act: Action, shop: Shop) -> Shop:
    """Deletes a list; its items move to the first remaining list. Returns that list."""
    shops = shops_of(db, shop.space_id)
    others = [s for s in shops if s.id != shop.id]
    if not others:
        raise ShopError(_("Die letzte Einkaufsliste kann nicht gelöscht werden."))
    target = others[0]
    for item in db.scalars(select(ShoppingItem).where(ShoppingItem.shop_id == shop.id)):
        act.updating(item)
        item.shop_id = target.id
    for row in db.scalars(select(CategoryOrder).where(CategoryOrder.shop_id == shop.id)):
        act.deleting(row)
    db.flush()
    act.deleting(shop)
    return target


def set_shop_order(db: Session, act: Action, space_id: int, ordered_ids: list[int]) -> bool:
    """Order of the lists on the shopping page; lists missing from `ordered_ids` keep their order at the end."""
    shops = shops_of(db, space_id)
    by_id = {s.id: s for s in shops}
    wanted = [by_id[i] for i in dict.fromkeys(ordered_ids) if i in by_id]
    ordered = wanted + [s for s in shops if s not in wanted]
    changed = False
    for position, shop in enumerate(ordered):
        if shop.position != position:
            act.updating(shop)
            shop.position = position
            changed = True
    return changed


def move_shop(db: Session, act: Action, shop: Shop, direction: str) -> bool:
    shops = shops_of(db, shop.space_id)
    index = next(i for i, s in enumerate(shops) if s.id == shop.id)
    target = index - 1 if direction == "up" else index + 1
    if not 0 <= target < len(shops):
        return False
    shops[index], shops[target] = shops[target], shops[index]
    return set_shop_order(db, act, shop.space_id, [s.id for s in shops])


def fallback_logo_svg(preset: Preset) -> str:
    """Logo tile (SVG) when the favicon can't be fetched. Only fixed preset data, no user input."""
    size = 17 if len(preset.text) == 1 else 14
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
        f'<rect width="32" height="32" rx="7" fill="{preset.color}"/>'
        f'<text x="16" y="{16 + size * 0.36:.1f}" font-family="system-ui,Arial,sans-serif" font-size="{size}" '
        f'font-weight="700" text-anchor="middle" fill="{preset.text_color}">{preset.text}</text></svg>'
    )
