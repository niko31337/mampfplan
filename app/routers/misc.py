from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings, today
from app.db import get_db
from app.models import Member, Space
from app.services import households, history, stats
from app.services.seed import share_url
from app.web import Ctx, get_ctx, get_space, redirect, render, set_member_cookie, templates

router = APIRouter(prefix="/s/{token}")

NameField = Annotated[str, Form(max_length=households.MAX_NAME_LENGTH * 2)]
HouseholdNameField = Annotated[str, Form(max_length=households.MAX_HOUSEHOLD_NAME_LENGTH * 2)]


def _require_owner(ctx: Ctx) -> None:
    if not households.is_owner(ctx.db, ctx.member):
        raise HTTPException(403, "Nur das erste Profil des Haushalts darf das.")


def _require_admin(ctx: Ctx) -> None:
    if not ctx.space.is_admin:
        raise HTTPException(404, "Nicht gefunden")  # Funktion für andere Haushalte unsichtbar


@router.get("")
def space_root(space: Annotated[Space, Depends(get_space)]):
    return redirect(f"/s/{space.token}/plan")


@router.get("/who")
def who_page(request: Request, space: Annotated[Space, Depends(get_space)], db: Annotated[Session, Depends(get_db)]):
    members = db.scalars(select(Member).where(Member.space_id == space.id).order_by(Member.id)).all()
    return templates.TemplateResponse(
        request, "who.html", {"space": space, "members": members, "base": f"/s/{space.token}", "member": None, "undo": None}
    )


@router.post("/who")
def choose_member(
    space: Annotated[Space, Depends(get_space)],
    db: Annotated[Session, Depends(get_db)],
    member_id: Annotated[int, Form()],
):
    member = db.get(Member, member_id)
    if member is None or member.space_id != space.id:
        raise HTTPException(400, "Unbekanntes Profil")
    response = redirect(f"/s/{space.token}/plan")
    set_member_cookie(response, member)
    return response


def _more(ctx: Ctx, error: str | None = None, status_code: int = 200):
    return render(
        ctx,
        "more.html",
        status_code=status_code,
        share_link=share_url(get_settings(), ctx.space),
        members=households.members_of(ctx.db, ctx.space.id),
        is_owner=households.is_owner(ctx.db, ctx.member),
        error=error,
    )


@router.get("/more")
def more_page(ctx: Annotated[Ctx, Depends(get_ctx)]):
    return _more(ctx)


@router.post("/members")
def add_member(ctx: Annotated[Ctx, Depends(get_ctx)], name: NameField):
    _require_owner(ctx)
    try:
        households.add_member(ctx.db, ctx.space, name)
    except households.HouseholdError as exc:
        return _more(ctx, error=str(exc), status_code=400)
    return redirect(f"{ctx.base}/more")


@router.post("/link/rotate")
def rotate_own_link(ctx: Annotated[Ctx, Depends(get_ctx)]):
    """Neuer Link für den eigenen Haushalt (z. B. wenn der alte in falsche Hände geraten ist)."""
    _require_owner(ctx)
    households.rotate_token(ctx.db, ctx.space)
    return redirect(f"/s/{ctx.space.token}/more")


def _households(ctx: Ctx, error: str | None = None, created: int | None = None, status_code: int = 200):
    settings = get_settings()
    infos = households.list_households(ctx.db)
    return render(
        ctx,
        "households.html",
        status_code=status_code,
        households=[(info, share_url(settings, info.space)) for info in infos],
        created=created,
        error=error,
    )


@router.get("/households")
def households_page(ctx: Annotated[Ctx, Depends(get_ctx)], created: int | None = None):
    _require_admin(ctx)
    return _households(ctx, created=created)


@router.post("/households")
def create_household(ctx: Annotated[Ctx, Depends(get_ctx)], name: HouseholdNameField, owner_name: NameField):
    _require_admin(ctx)
    try:
        space = households.create_household(ctx.db, name, owner_name)
    except households.HouseholdError as exc:
        return _households(ctx, error=str(exc), status_code=400)
    return redirect(f"{ctx.base}/households?created={space.id}")


@router.post("/households/{space_id}/rotate")
def rotate_household_link(ctx: Annotated[Ctx, Depends(get_ctx)], space_id: int):
    _require_admin(ctx)
    target = ctx.db.get(Space, space_id)
    if target is None:
        raise HTTPException(404, "Haushalt nicht gefunden")
    households.rotate_token(ctx.db, target)
    # Eigener Haushalt: der bisherige Link (und damit diese Seite) ist jetzt ungültig
    return redirect(f"/s/{ctx.space.token}/households")


@router.get("/history")
def history_page(ctx: Annotated[Ctx, Depends(get_ctx)]):
    return render(ctx, "history.html", entries=history.recent(ctx.db, ctx.space.id))


@router.get("/stats")
def stats_page(ctx: Annotated[Ctx, Depends(get_ctx)]):
    day = today()
    total, distinct = stats.totals(ctx.db, ctx.space.id, day)
    return render(
        ctx,
        "stats.html",
        total=total,
        distinct=distinct,
        top=stats.top_recipes(ctx.db, ctx.space.id, day),
        recent=stats.cooked_history(ctx.db, ctx.space.id, day),
    )


@router.post("/undo")
def undo(ctx: Annotated[Ctx, Depends(get_ctx)]):
    history.undo_last(ctx.db, ctx.space.id)
    if ctx.request.headers.get("HX-Request") == "true":
        return Response(status_code=204, headers={"HX-Refresh": "true"})
    return redirect(f"{ctx.base}/plan")


@router.get("/manifest.webmanifest")
def manifest(space: Annotated[Space, Depends(get_space)]):
    return JSONResponse(
        {
            "name": "Mampfplan",
            "short_name": "Mampfplan",
            "start_url": f"/s/{space.token}/plan",
            "display": "standalone",
            "background_color": "#fffaf3",
            "theme_color": "#e8590c",
            "icons": [{"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}],
        },
        media_type="application/manifest+json",
    )
