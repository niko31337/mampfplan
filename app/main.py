import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app import i18n
from app.i18n import _, translate
from app.db import Base, SessionLocal, engine, migrate
from app.routers import api, images, misc, plan, recipes, search, shopping
from app.services.seed import ensure_space, seed_catalog
from app.web import (
    ASSET_VERSION,
    BASE_DIR,
    COOKIE_MAX_AGE,
    CSRF_COOKIE,
    SECURITY_HEADERS,
    NeedsProfile,
    templates,
    verify_csrf,
)

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    migrate(engine)  # vor create_all: ergänzt Spalten an bestehenden Tabellen
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_catalog(db)
        ensure_space(db, get_settings())
    yield


# Keine interaktive API-Dokumentation: reduziert die Angriffsfläche.
app = FastAPI(
    title="Mampfplan",
    lifespan=lifespan,
    dependencies=[Depends(verify_csrf)],
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

for module in (api, images, misc, plan, recipes, search, shopping):
    app.include_router(module.router)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Sprache wählen, CSRF-Cookie ausstellen und Security-Header setzen."""
    lang_token = i18n.set_language(i18n.pick_language(request.cookies, request.headers))
    csrf = request.cookies.get(CSRF_COOKIE)
    new_cookie = csrf is None
    if new_cookie:
        csrf = secrets.token_urlsafe(32)
    request.state.csrf_token = csrf

    try:
        response = await call_next(request)
    finally:
        i18n.reset_language(lang_token)

    if new_cookie and not request.url.path.startswith("/static"):
        response.set_cookie(
            CSRF_COOKIE,
            csrf,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=get_settings().secure_cookies,
        )
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if not request.url.path.startswith("/static"):
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("Content-Language", i18n.pick_language(request.cookies, request.headers))
    return response


def _safe_next(target: str) -> str:
    """Nur eigene relative Pfade als Rücksprung (kein Open Redirect über //host oder Schema)."""
    if not target.startswith("/") or target.startswith(("//", "/\\")) or len(target) > 500:
        return "/"
    if any(c in target for c in "\r\n\\"):
        return "/"
    return target


@app.post("/lang")
def switch_language(lang: str = Form(max_length=5), next: str = Form("/", max_length=500)):  # noqa: A002
    """Sprache für dieses Gerät umstellen (Cookie, ein Jahr gültig)."""
    response = RedirectResponse(_safe_next(next), status_code=303)
    if lang in i18n.LANGUAGES:
        response.set_cookie(
            i18n.LANG_COOKIE, lang, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax", secure=get_settings().secure_cookies
        )
    return response


@app.exception_handler(NeedsProfile)
async def needs_profile_handler(request: Request, exc: NeedsProfile):
    url = f"/s/{exc.token}/who"
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=204, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def _error_page(request: Request, status: int, message: str):
    return templates.TemplateResponse(request, "error.html", {"status": status, "message": message}, status_code=status)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    message = _("Das haben wir nicht gefunden.") if exc.status_code == 404 else translate(str(exc.detail))
    return _error_page(request, exc.status_code, message)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, _exc: RequestValidationError):
    return _error_page(request, 422, _("Eingabe ungültig oder zu lang."))


@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    return templates.TemplateResponse(request, "landing.html", {})


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    # Muss unter "/" ausgeliefert werden, damit der Worker alle Seiten (/s/…) abdecken darf.
    # Die eingesetzte Versionskennung sorgt dafür, dass sich der Worker bei jedem Update erneuert.
    script = (BASE_DIR / "static" / "sw.js").read_text(encoding="utf-8").replace("__ASSET_VERSION__", ASSET_VERSION)
    return Response(
        script,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


@app.get("/offline", response_class=HTMLResponse)
def offline_page(request: Request):
    return templates.TemplateResponse(request, "offline.html", {})


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return "User-agent: *\nDisallow: /\n"
