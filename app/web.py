"""Gemeinsame Web-Helfer: Templates, Zugriffsprüfung (Token + Profil), CSRF."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings, today
from app.db import get_db
from app.models import Member, Space
from app.services import history
from app.services.images import cached_image_src
from app.services.ingredients import format_amount, format_quantity

BASE_DIR = Path(__file__).resolve().parent
CSRF_COOKIE = "mp_csrf"
MEMBER_COOKIE = "mp_member"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]

templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _asset_version() -> str:
    """Fingerabdruck der statischen Dateien. Ändert sich mit jedem Update, damit Browser und Service Worker
    nie eine neue Seite mit altem CSS/JS kombinieren."""
    digest = hashlib.sha256()
    for path in sorted((BASE_DIR / "static").iterdir()):
        if path.is_file() and path.name != "sw.js":
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


ASSET_VERSION = _asset_version()


def asset_url(name: str) -> str:
    return f"/static/{name}?v={ASSET_VERSION}"


templates.env.globals["asset"] = asset_url


def _date_short(d: date) -> str:
    return d.strftime("%d.%m.")


def _date_long(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]}, {d.day}. {MONTHS[d.month - 1]}"


templates.env.filters["date_short"] = _date_short
templates.env.filters["date_long"] = _date_long
templates.env.filters["amount"] = format_amount
templates.env.filters["quantity"] = format_quantity
templates.env.filters["cached_img"] = cached_image_src
templates.env.globals["weekdays"] = WEEKDAYS


class NeedsProfile(Exception):
    """Der Besucher hat noch kein Profil (Mitglied) gewählt."""

    def __init__(self, token: str):
        self.token = token


@dataclass
class Ctx:
    request: Request
    db: Session
    space: Space
    member: Member

    @property
    def base(self) -> str:
        return f"/s/{self.space.token}"

    @property
    def is_partial(self) -> bool:
        """True, wenn nur ein Seitenfragment gewünscht ist (HTMX-Request, aber kein Boost)."""
        headers = self.request.headers
        return headers.get("HX-Request") == "true" and headers.get("HX-Boosted") != "true"


def get_space(token: str, db: Session = Depends(get_db)) -> Space:
    space = db.scalars(select(Space).where(Space.token == token)).first()
    if space is None:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return space


def member_cookie_name(space_id: int) -> str:
    """Eigenes Profil-Cookie je Haushalt, damit ein Gerät mehrere Haushalte nutzen kann."""
    return f"{MEMBER_COOKIE}_{space_id}"


def get_ctx(request: Request, space: Space = Depends(get_space), db: Session = Depends(get_db)) -> Ctx:
    raw = request.cookies.get(member_cookie_name(space.id), "")
    member = db.get(Member, int(raw)) if raw.isdigit() else None
    if member is None or member.space_id != space.id:
        raise NeedsProfile(space.token)
    return Ctx(request=request, db=db, space=space, member=member)


def render(ctx: Ctx, template: str, status_code: int = 200, **context) -> HTMLResponse:
    context.update(
        space=ctx.space,
        member=ctx.member,
        base=ctx.base,
        today=today(),
        undo=history.last_undoable(ctx.db, ctx.space.id),
        oob=ctx.is_partial,  # Fragment-Antwort: Undo-Button/Toast per Out-of-band-Swap mitliefern
    )
    return templates.TemplateResponse(ctx.request, template, context, status_code=status_code)


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def set_member_cookie(response: Response, member: Member) -> None:
    response.set_cookie(
        member_cookie_name(member.space_id),
        str(member.id),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=get_settings().secure_cookies,
    )


async def verify_csrf(request: Request) -> None:
    """Double-Submit-Cookie: Header `X-CSRF-Token` (HTMX) oder Formularfeld `csrf_token`."""
    if request.method in SAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    supplied = request.headers.get("X-CSRF-Token")
    if not supplied:
        form = await request.form()
        supplied = form.get("csrf_token")
    if not cookie or not isinstance(supplied, str) or not secrets.compare_digest(cookie, supplied):
        raise HTTPException(status_code=403, detail="CSRF-Prüfung fehlgeschlagen. Seite neu laden und erneut versuchen.")


SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "worker-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'; object-src 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "X-Robots-Tag": "noindex, nofollow",
}
