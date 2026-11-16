"""Shared web helpers: templates, access check (token + profile), CSRF."""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import i18n
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

WEEKDAYS = i18n.WEEKDAYS["de"]  # API default (Home Assistant)

templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.add_extension("jinja2.ext.i18n")
templates.env.install_gettext_callables(i18n.gettext, i18n.ngettext, newstyle=True)


def _asset_version() -> str:
    """Fingerprint of the static files. Changes with every update so the browser and service worker
    never combine a new page with old CSS/JS."""
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


_LINK = re.compile(r"https://[^\s<>\"']+")


def url_host(url: str | None) -> str:
    host = (urlsplit(url or "").hostname or "").lower()
    return host.removeprefix("www.")


def linkify(text: str | None) -> Markup:
    """Output text safely and make https links in it clickable (e.g. „Quelle: https://…“)."""
    text = text or ""
    parts, last = [], 0
    for match in _LINK.finditer(text):
        url = match[0].rstrip(".,;:!?)")
        end = match.start() + len(url)
        parts.append(escape(text[last : match.start()]))
        parts.append(Markup('<a href="{0}" rel="noopener noreferrer nofollow" target="_blank">{0}</a>').format(url))
        last = end
    parts.append(escape(text[last:]))
    return Markup("").join(parts)


SOURCE_LABELS = {"manual": "", "chefkoch": "Chefkoch", "mealdb": "TheMealDB", "spoonacular": "Spoonacular"}


def source_label(recipe) -> str:
    """Display name of the origin: known sources by name, links by their domain."""
    if recipe.source in SOURCE_LABELS:
        return SOURCE_LABELS[recipe.source]
    return url_host(recipe.source_url) or recipe.source


def _date_short(d: date) -> str:
    return i18n.date_short(d)


def _date_long(d: date) -> str:
    return i18n.date_long(d)


def category_label(category: str) -> str:
    """Categories are German internally (database, store order); the translation is displayed."""
    return i18n.translate(category)


def describe(description: str) -> str:
    """Display a history entry: new entries are stored translatable, old ones as plain German text."""
    return history.describe(description)


# texts app.js needs (as a JSON data block in base.html, no inline script)
JS_TEXTS = [
    "Alle wählen", "Keine wählen", "Kopiert ✓", "📴 Gemerkt – wird übertragen, sobald du wieder Verbindung hast.",
    "Keine Verbindung zum Server.", "Sitzung abgelaufen – bitte Seite neu laden.", "Das hat leider nicht geklappt.",
    "✓ Änderungen synchronisiert",
    "🔄 %(num)s Änderung wird übertragen …", "🔄 %(num)s Änderungen werden übertragen …",
    "📴 Keine Verbindung – %(num)s Änderung wartet auf Übertragung", "📴 Keine Verbindung – %(num)s Änderungen warten auf Übertragung",
    "📴 Offline – Abhaken und Ansehen funktioniert weiterhin", "Gekauft",
    "in den Wagen gelegt von %(name)s", "eingetragen von %(name)s",
    "Display-Sperre nicht verfügbar (benötigt HTTPS)", "🔆 Display bleibt an, solange diese Seite offen ist",
]  # fmt: skip


def js_texts() -> dict[str, str]:
    from app.services.shopping import CATEGORY_ORDER  # category names for newly created groups

    return {msgid: i18n.translate(msgid) for msgid in [*JS_TEXTS, *CATEGORY_ORDER]}


templates.env.filters["date_short"] = _date_short
templates.env.filters["date_long"] = _date_long
templates.env.filters["category"] = category_label
templates.env.filters["date_numeric"] = i18n.date_numeric
templates.env.filters["describe"] = describe
templates.env.globals["js_texts"] = js_texts
templates.env.globals["current_lang"] = i18n.get_language
templates.env.globals["languages"] = i18n.LANGUAGES
templates.env.filters["amount"] = format_amount
templates.env.filters["quantity"] = format_quantity
templates.env.filters["cached_img"] = cached_image_src
templates.env.filters["host"] = url_host
templates.env.filters["linkify"] = linkify
templates.env.filters["source_label"] = source_label
templates.env.globals["weekday_name"] = i18n.weekday_name


def _shop_name(shop) -> str:
    from app.services.shops import display_name

    return display_name(shop)


def _shop_preset(shop):
    from app.services.shops import preset_of

    return preset_of(shop)


def _shop_initials(shop) -> str:
    from app.services.shops import initials

    return initials(shop)


templates.env.filters["shop_name"] = _shop_name
templates.env.globals["shop_preset"] = _shop_preset
templates.env.globals["shop_initials"] = _shop_initials


class NeedsProfile(Exception):
    """The visitor hasn't picked a profile (member) yet."""

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
        """True if only a page fragment is wanted (HTMX request, but no boost)."""
        headers = self.request.headers
        return headers.get("HX-Request") == "true" and headers.get("HX-Boosted") != "true"


def get_space(token: str, db: Session = Depends(get_db)) -> Space:
    space = db.scalars(select(Space).where(Space.token == token)).first()
    if space is None:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return space


def member_cookie_name(space_id: int) -> str:
    """Separate profile cookie per household so one device can use several households."""
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
        oob=ctx.is_partial,  # fragment response: include the undo button/toast via out-of-band swap
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
    """Double-submit cookie: header `X-CSRF-Token` (HTMX) or form field `csrf_token`."""
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
