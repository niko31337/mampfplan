from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from sqlalchemy import select

from app.config import now
from app.models import CatalogItem, ShoppingItem
from app.services import households, history
from app.services import shopping as service
from app.services.ingredients import parse_line
from app.web import Ctx, get_ctx, render

router = APIRouter(prefix="/s/{token}")

MAX_CHECKED_SHOWN = 40
MAX_IDS = 500


def _list_context(ctx: Ctx) -> dict:
    items = ctx.db.scalars(select(ShoppingItem).where(ShoppingItem.space_id == ctx.space.id)).all()
    order = {name: rank for rank, name in enumerate(service.category_order(ctx.db, ctx.space.id))}
    open_items = sorted(
        (i for i in items if not i.checked),
        key=lambda i: (order.get(i.category, len(order)), *service.item_sort_key(i)),
    )
    groups: dict[str, list[ShoppingItem]] = {}
    for item in open_items:
        groups.setdefault(item.category, []).append(item)
    checked = sorted((i for i in items if i.checked), key=lambda i: i.checked_at or now(), reverse=True)
    known = households.badges([m.name for m in households.members_of(ctx.db, ctx.space.id)])
    # Alle Kategorien in Haushalts-Reihenfolge: leere dienen beim Ziehen als Ablagefläche
    categories = list(order) + [c for c in groups if c not in order]
    return {
        "categories": categories,
        "groups": groups,
        "open_count": len(open_items),
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
    """Nur die Liste (für die Synchronisation nach Offline-Phasen)."""
    return _respond_list(ctx)


@router.get("/shopping/suggest")
def suggest(ctx: Annotated[Ctx, Depends(get_ctx)], q: Annotated[str, Query(alias="name", max_length=100)] = ""):
    items = service.search_catalog(ctx.db, q) if q.strip() else []
    return render(ctx, "partials/suggestions.html", items=items, q=q.strip())


@router.get("/shopping/catalog")
def catalog(ctx: Annotated[Ctx, Depends(get_ctx)], q: Annotated[str, Query(max_length=100)] = ""):
    items = service.search_catalog(ctx.db, q, limit=300) if q.strip() else list(
        ctx.db.scalars(select(CatalogItem).order_by(CatalogItem.name))
    )
    groups: dict[str, list[CatalogItem]] = {}
    for item in sorted(items, key=lambda i: (service.category_rank(i.category), i.name.casefold())):
        groups.setdefault(item.category, []).append(item)
    on_list = {
        i.name.lower()
        for i in ctx.db.scalars(
            select(ShoppingItem).where(ShoppingItem.space_id == ctx.space.id, ShoppingItem.checked.is_(False))
        )
    }
    return render(ctx, "catalog.html", q=q, groups=groups, on_list=on_list)


@router.post("/shopping/add")
def add(
    ctx: Annotated[Ctx, Depends(get_ctx)],
    name: Annotated[str, Form(max_length=120)],
    category: Annotated[str, Form(max_length=40)] = "",
    target: Annotated[str, Form(pattern="^(list|chip)$")] = "list",
):
    amount, unit, clean_name = parse_line(name)
    clean_name = service.clean_name(clean_name)
    if not clean_name:
        return _respond_list(ctx, clear_suggestions=True)
    category = category if category in service.CATEGORY_ORDER else None
    with history.record(ctx.db, ctx.space.id, ctx.member.name, f"„{clean_name[:60]}“ auf die Einkaufsliste gesetzt") as act:
        item, merged = service.add_item(
            ctx.db, act, ctx.space.id, clean_name, amount, unit, category, added_by=ctx.member.name
        )
    if target == "chip":
        return render(ctx, "partials/chip_added.html", name=item.name)
    return _respond_list(ctx, f"„{item.name}“ hinzugefügt", clear_suggestions=True)


# Reihenfolge: vor "/shopping/{item_id}/…" deklarieren, damit "reorder"/"categories" nicht als item_id gelesen werden.
@router.post("/shopping/reorder")
def reorder(
    ctx: Annotated[Ctx, Depends(get_ctx)],
    category: Annotated[str, Form(max_length=40)],
    ids: Annotated[list[int] | None, Form()] = None,
):
    if category not in service.CATEGORY_ORDER:
        raise HTTPException(400, "Unbekannte Kategorie")
    ordered = (ids or [])[:MAX_IDS]
    with history.record(ctx.db, ctx.space.id, ctx.member.name, f"Artikel in „{category}“ umsortiert") as act:
        service.reorder_items(ctx.db, act, ctx.space.id, category, ordered)
    return _respond_list(ctx)


@router.get("/shopping/categories")
def store_order_page(ctx: Annotated[Ctx, Depends(get_ctx)]):
    """Alle Kategorien in Ladenreihenfolge sortieren – auch die, die gerade leer sind."""
    counts: dict[str, int] = {}
    for item in ctx.db.scalars(
        select(ShoppingItem).where(ShoppingItem.space_id == ctx.space.id, ShoppingItem.checked.is_(False))
    ):
        counts[item.category] = counts.get(item.category, 0) + 1
    return render(ctx, "categories.html", categories=service.category_order(ctx.db, ctx.space.id), counts=counts)


CategoryField = Annotated[str, Form(max_length=40)]


@router.post("/shopping/categories/order")
def order_categories(
    ctx: Annotated[Ctx, Depends(get_ctx)],
    categories: Annotated[list[str] | None, Form()] = None,
    category: CategoryField = "",
    before: CategoryField = "",
    after: CategoryField = "",
):
    """Entweder eine Kategorie vor/hinter eine andere setzen (Ziehen in der Liste)
    oder eine vollständige Reihenfolge übernehmen (Seite „Ladenreihenfolge“)."""
    with history.record(ctx.db, ctx.space.id, ctx.member.name, "Kategorien umsortiert") as act:
        if category:
            if category not in service.CATEGORY_ORDER:
                raise HTTPException(400, "Unbekannte Kategorie")
            service.move_category(ctx.db, act, ctx.space.id, category, before or None, after or None)
        else:
            wanted = [c for c in (categories or []) if c in service.CATEGORY_ORDER]
            service.set_category_order(ctx.db, act, ctx.space.id, wanted)
    return _respond_list(ctx)


@router.post("/shopping/clear-checked")
def clear_checked(ctx: Annotated[Ctx, Depends(get_ctx)]):
    checked = ctx.db.scalars(
        select(ShoppingItem).where(ShoppingItem.space_id == ctx.space.id, ShoppingItem.checked.is_(True))
    ).all()
    if checked:
        with history.record(ctx.db, ctx.space.id, ctx.member.name, f"{len(checked)} erledigte Artikel entfernt") as act:
            for item in checked:
                act.deleting(item)
    return _respond_list(ctx, f"{len(checked)} Artikel entfernt" if checked else None)


@router.post("/shopping/{item_id}/toggle")
def toggle(
    ctx: Annotated[Ctx, Depends(get_ctx)],
    item_id: int,
    state: Annotated[str, Form(pattern="^[01]$")] = "",
):
    """Setzt den Haken. Mit `state` (1/0) idempotent – wichtig für das erneute Senden nach Offline-Phasen."""
    item = _get_item(ctx, item_id)
    wanted = (not item.checked) if state == "" else state == "1"
    if wanted != item.checked:
        label = "abgehakt" if wanted else "wieder auf die Liste gesetzt"
        with history.record(ctx.db, ctx.space.id, ctx.member.name, f"„{item.name[:60]}“ {label}") as act:
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
):
    """Artikel per Drag & Drop in eine andere Kategorie (und dort an eine Stelle) schieben."""
    item = _get_item(ctx, item_id)
    if category not in service.CATEGORY_ORDER:
        raise HTTPException(400, "Unbekannte Kategorie")
    if item.checked:
        raise HTTPException(400, "Gekaufte Artikel lassen sich nicht verschieben")
    label = f"„{item.name[:60]}“ nach „{category}“ verschoben" if item.category != category else f"„{item.name[:60]}“ verschoben"
    with history.record(ctx.db, ctx.space.id, ctx.member.name, label) as act:
        service.move_to_category(ctx.db, act, ctx.space.id, item, category, (ids or [])[:MAX_IDS])
    return _respond_list(ctx)


@router.post("/shopping/{item_id}/delete")
def delete(ctx: Annotated[Ctx, Depends(get_ctx)], item_id: int):
    item = _get_item(ctx, item_id)
    name = item.name
    with history.record(ctx.db, ctx.space.id, ctx.member.name, f"„{name[:60]}“ von der Einkaufsliste entfernt") as act:
        act.deleting(item)
    return _respond_list(ctx, f"„{name[:40]}“ entfernt")
