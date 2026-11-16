from dataclasses import dataclass, field
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from sqlalchemy import select

from app.config import now
from app.i18n import N_, _
from app.models import Shop, ShoppingItem
from app.services import households, history
from app.services import shopping as service
from app.services import shops as shop_service
from app.services.ingredients import parse_line
from app.web import Ctx, get_ctx, render

router = APIRouter(prefix="/s/{token}")

MAX_CHECKED_SHOWN = 40
MAX_IDS = 500
CLOSED_COOKIE = "mp_closed"  # + household id: lists collapsed on this device


@dataclass
class ShopView:
    """One list (shop) on the shopping page."""

    shop: Shop
    categories: list[str]
    groups: dict[str, list[ShoppingItem]] = field(default_factory=dict)
    open_count: int = 0
    is_open: bool = True


def closed_cookie_name(space_id: int) -> str:
    return f"{CLOSED_COOKIE}_{space_id}"


def _closed_shops(ctx: Ctx) -> set[int]:
    raw = ctx.request.cookies.get(closed_cookie_name(ctx.space.id), "")
    return {int(part) for part in raw.split(".") if part.isdigit()}


def shop_error(exc: shop_service.ShopError) -> HTTPException:
    """400 with the message; app.js shows it as a toast (header X-Mampf-Message)."""
    return HTTPException(400, str(exc), headers={"X-Mampf-Message": quote(str(exc))})


def _resolve_shop(ctx: Ctx, shop_id: int | None) -> Shop:
    try:
        return shop_service.resolve_shop(ctx.db, ctx.space.id, shop_id)
    except shop_service.ShopError as exc:
        raise shop_error(exc) from exc


def _list_context(ctx: Ctx) -> dict:
    shops = shop_service.shops_of(ctx.db, ctx.space.id)
    items = ctx.db.scalars(select(ShoppingItem).where(ShoppingItem.space_id == ctx.space.id)).all()
    closed = _closed_shops(ctx) if len(shops) > 1 else set()
    views = []
    for shop in shops:
        order = {name: rank for rank, name in enumerate(service.category_order(ctx.db, ctx.space.id, shop.id))}
        open_items = sorted(
            (i for i in items if not i.checked and i.shop_id == shop.id),
            key=lambda i: (order.get(i.category, len(order)), *service.item_sort_key(i)),
        )
        groups: dict[str, list[ShoppingItem]] = {}
        for item in open_items:
            groups.setdefault(item.category, []).append(item)
        # all categories in the list's order: empty ones serve as drop targets while dragging
        categories = list(order) + [c for c in groups if c not in order]
        views.append(ShopView(shop, categories, groups, len(open_items), shop.id not in closed))
    checked = sorted((i for i in items if i.checked), key=lambda i: i.checked_at or now(), reverse=True)
    known = households.badges([m.name for m in households.members_of(ctx.db, ctx.space.id)])
    return {
        "shop_views": views,
        "shops": shops,
        "shops_by_id": {s.id: s for s in shops},
        "multi": len(shops) > 1,
        "open_count": sum(v.open_count for v in views),
        "checked": checked[:MAX_CHECKED_SHOWN],
        "badge": lambda name: households.badge_for(name, known),
        "my_badge": households.badge_for(ctx.member.name, known),
    }


def _respond_list(ctx: Ctx, toast: str | None = None, clear_suggestions: bool = False):
    return render(
        ctx, "partials/shopping_list.html", toast=toast, clear_suggestions=clear_suggestions, **_list_context(ctx)
    )


def _get_item(ctx: Ctx, item_id: int) -> ShoppingItem:
    item = ctx.db.get(ShoppingItem, item_id)
    if item is None or item.space_id != ctx.space.id:
        raise HTTPException(404, "Artikel nicht gefunden")
    return item


@router.get("/shopping")
def shopping_page(ctx: Annotated[Ctx, Depends(get_ctx)]):
    return render(ctx, "shopping.html", clear_suggestions=False, **_list_context(ctx))


@router.get("/shopping/list")
def shopping_list_fragment(ctx: Annotated[Ctx, Depends(get_ctx)]):
    """Only the list (for syncing after offline periods)."""
    return _respond_list(ctx)


@router.get("/shopping/suggest")
def suggest(ctx: Annotated[Ctx, Depends(get_ctx)], q: Annotated[str, Query(alias="name", max_length=100)] = ""):
    items = service.search_catalog(ctx.db, q) if q.strip() else []
    return render(ctx, "partials/suggestions.html", items=items, q=q.strip())


@router.get("/shopping/catalog")
def catalog(ctx: Annotated[Ctx, Depends(get_ctx)], q: Annotated[str, Query(max_length=100)] = ""):
    items = service.search_catalog(ctx.db, q, limit=300) if q.strip() else service.catalog_entries(ctx.db)
    groups: dict[str, list[service.CatalogEntry]] = {}
    for item in sorted(items, key=lambda i: (service.category_rank(i.category), i.name.casefold())):
        groups.setdefault(item.category, []).append(item)
    shops = shop_service.shops_of(ctx.db, ctx.space.id)
    on_list = {
        i.name.lower()
        for i in ctx.db.scalars(
            select(ShoppingItem).where(ShoppingItem.space_id == ctx.space.id, ShoppingItem.checked.is_(False))
        )
    }
    return render(ctx, "catalog.html", q=q, groups=groups, on_list=on_list, shops=shops, multi=len(shops) > 1)


@router.post("/shopping/add")
def add(
    ctx: Annotated[Ctx, Depends(get_ctx)],
    name: Annotated[str, Form(max_length=120)],
    category: Annotated[str, Form(max_length=40)] = "",
    target: Annotated[str, Form(pattern="^(list|chip)$")] = "list",
    shop_id: Annotated[int | None, Form()] = None,
):
    amount, unit, clean_name = parse_line(name)
    clean_name = service.clean_name(clean_name)
    if not clean_name:
        return _respond_list(ctx, clear_suggestions=True)
    shop = _resolve_shop(ctx, shop_id)
    category = category if category in service.CATEGORY_ORDER else None
    with history.record(ctx.db, ctx.space.id, ctx.member.name, history.msg("„%(name)s“ auf die Einkaufsliste gesetzt", name=clean_name[:60])) as act:
        item, merged = service.add_item(
            ctx.db, act, ctx.space.id, clean_name, amount, unit, category, added_by=ctx.member.name, shop_id=shop.id
        )
    if target == "chip":
        return render(ctx, "partials/chip_added.html", name=item.name)
    if len(shop_service.shops_of(ctx.db, ctx.space.id)) > 1:
        toast = _("„%(name)s“ auf %(shop)s gesetzt", name=item.name, shop=shop_service.display_name(shop))
    else:
        toast = _("„%(name)s“ hinzugefügt", name=item.name)
    return _respond_list(ctx, toast, clear_suggestions=True)


# Order: declare before "/shopping/{item_id}/…" so "reorder"/"categories" aren't read as item_id.
@router.post("/shopping/reorder")
def reorder(
    ctx: Annotated[Ctx, Depends(get_ctx)],
    category: Annotated[str, Form(max_length=40)],
    ids: Annotated[list[int] | None, Form()] = None,
    shop_id: Annotated[int | None, Form()] = None,
):
    if category not in service.CATEGORY_ORDER:
        raise HTTPException(400, "Unbekannte Kategorie")
    shop = _resolve_shop(ctx, shop_id)
    ordered = (ids or [])[:MAX_IDS]
    with history.record(ctx.db, ctx.space.id, ctx.member.name, history.msg("Artikel in „%(category)s“ umsortiert", category=category)) as act:
        service.reorder_items(ctx.db, act, ctx.space.id, shop.id, category, ordered)
    return _respond_list(ctx)


@router.get("/shopping/categories")
def store_order_page(ctx: Annotated[Ctx, Depends(get_ctx)], shop: int | None = None):
    """Sort all categories of a list in store order, including those that are currently empty."""
    shops = shop_service.shops_of(ctx.db, ctx.space.id)
    current = shop_service.get_shop(ctx.db, ctx.space.id, shop) or shops[0]
    counts: dict[str, int] = {}
    for item in ctx.db.scalars(
        select(ShoppingItem).where(
            ShoppingItem.space_id == ctx.space.id,
            ShoppingItem.shop_id == current.id,
            ShoppingItem.checked.is_(False),
        )
    ):
        counts[item.category] = counts.get(item.category, 0) + 1
    return render(
        ctx,
        "categories.html",
        categories=service.category_order(ctx.db, ctx.space.id, current.id),
        counts=counts,
        shops=shops,
        current=current,
    )


CategoryField = Annotated[str, Form(max_length=40)]


@router.post("/shopping/categories/order")
def order_categories(
    ctx: Annotated[Ctx, Depends(get_ctx)],
    categories: Annotated[list[str] | None, Form()] = None,
    category: CategoryField = "",
    before: CategoryField = "",
    after: CategoryField = "",
    shop_id: Annotated[int | None, Form()] = None,
):
    """Either move a category before/after another one (dragging in the list)
    or take over a complete order (page „Store order“) – always for one list (shop)."""
    shop = _resolve_shop(ctx, shop_id)
    with history.record(ctx.db, ctx.space.id, ctx.member.name, history.msg("Kategorien umsortiert")) as act:
        if category:
            if category not in service.CATEGORY_ORDER:
                raise HTTPException(400, "Unbekannte Kategorie")
            service.move_category(ctx.db, act, ctx.space.id, shop.id, category, before or None, after or None)
        else:
            wanted = [c for c in (categories or []) if c in service.CATEGORY_ORDER]
            service.set_category_order(ctx.db, act, ctx.space.id, shop.id, wanted)
    return _respond_list(ctx)


@router.post("/shopping/clear-checked")
def clear_checked(ctx: Annotated[Ctx, Depends(get_ctx)]):
    checked = ctx.db.scalars(
        select(ShoppingItem).where(ShoppingItem.space_id == ctx.space.id, ShoppingItem.checked.is_(True))
    ).all()
    if checked:
        with history.record(ctx.db, ctx.space.id, ctx.member.name, history.msg("%(count)s erledigte Artikel entfernt", count=len(checked))) as act:
            for item in checked:
                act.deleting(item)
    return _respond_list(ctx, _("%(count)s Artikel entfernt", count=len(checked)) if checked else None)


@router.post("/shopping/{item_id}/toggle")
def toggle(
    ctx: Annotated[Ctx, Depends(get_ctx)],
    item_id: int,
    state: Annotated[str, Form(pattern="^[01]$")] = "",
):
    """Sets the tick. Idempotent with `state` (1/0), which matters when resending after offline periods."""
    item = _get_item(ctx, item_id)
    wanted = (not item.checked) if state == "" else state == "1"
    if wanted != item.checked:
        text = N_("„%(name)s“ abgehakt") if wanted else N_("„%(name)s“ wieder auf die Liste gesetzt")
        with history.record(ctx.db, ctx.space.id, ctx.member.name, history.msg(text, name=item.name[:60])) as act:
            act.updating(item)
            item.checked = wanted
            item.checked_at = now() if wanted else None
            item.checked_by = ctx.member.name if wanted else None
    return _respond_list(ctx)


@router.post("/shopping/{item_id}/move")
def move_to_category(
    ctx: Annotated[Ctx, Depends(get_ctx)],
    item_id: int,
    category: Annotated[str, Form(max_length=40)],
    ids: Annotated[list[int] | None, Form()] = None,
    shop_id: Annotated[int | None, Form()] = None,
):
    """Move an item by drag & drop into another category (and to a position there), possibly on another list."""
    item = _get_item(ctx, item_id)
    if category not in service.CATEGORY_ORDER:
        raise HTTPException(400, "Unbekannte Kategorie")
    if item.checked:
        raise HTTPException(400, "Gekaufte Artikel lassen sich nicht verschieben")
    shop = _resolve_shop(ctx, shop_id) if shop_id else None
    label = (
        history.msg("„%(name)s“ nach „%(category)s“ verschoben", name=item.name[:60], category=category)
        if item.category != category
        else history.msg("„%(name)s“ verschoben", name=item.name[:60])
    )
    with history.record(ctx.db, ctx.space.id, ctx.member.name, label) as act:
        service.move_to_category(
            ctx.db, act, ctx.space.id, item, category, (ids or [])[:MAX_IDS], shop.id if shop else None
        )
    return _respond_list(ctx)


@router.post("/shopping/{item_id}/shop")
def move_to_shop(
    ctx: Annotated[Ctx, Depends(get_ctx)],
    item_id: int,
    shop_id: Annotated[int, Form()],
):
    """Move an item to another list (shop); it lands there in its category."""
    item = _get_item(ctx, item_id)
    shop = _resolve_shop(ctx, shop_id)
    if item.shop_id == shop.id:
        return _respond_list(ctx)
    shop_name = shop_service.display_name(shop)
    with history.record(
        ctx.db, ctx.space.id, ctx.member.name,
        history.msg("„%(name)s“ auf die Liste %(shop)s verschoben", name=item.name[:60], shop=shop_name[:50]),
    ) as act:
        moved = service.move_to_shop(ctx.db, act, ctx.space.id, item, shop.id)
    return _respond_list(ctx, _("„%(name)s“ → %(shop)s", name=moved.name[:40], shop=shop_name))


@router.post("/shopping/{item_id}/delete")
def delete(ctx: Annotated[Ctx, Depends(get_ctx)], item_id: int):
    item = _get_item(ctx, item_id)
    name = item.name
    with history.record(ctx.db, ctx.space.id, ctx.member.name, history.msg("„%(name)s“ von der Einkaufsliste entfernt", name=name[:60])) as act:
        act.deleting(item)
    return _respond_list(ctx, _("„%(name)s“ entfernt", name=name[:40]))
