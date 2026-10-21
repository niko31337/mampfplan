from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings, today
from app.db import get_db
from app.models import Member, Space
from app.services import history, stats
from app.services.seed import share_url
from app.web import Ctx, get_ctx, get_space, redirect, render, set_member_cookie, templates

router = APIRouter(prefix="/s/{token}")


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
    set_member_cookie(response, member.id)
    return response


@router.get("/more")
def more_page(ctx: Annotated[Ctx, Depends(get_ctx)]):
    return render(ctx, "more.html", share_link=share_url(get_settings(), ctx.space))


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
