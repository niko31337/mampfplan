import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.db import Base, SessionLocal, engine, migrate
from app.routers import images, misc, plan, recipes, search, shopping
from app.services.seed import ensure_space, seed_catalog
from app.web import (
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

for module in (images, misc, plan, recipes, search, shopping):
    app.include_router(module.router)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """CSRF-Cookie ausstellen und Security-Header setzen."""
    csrf = request.cookies.get(CSRF_COOKIE)
    new_cookie = csrf is None
    if new_cookie:
        csrf = secrets.token_urlsafe(32)
    request.state.csrf_token = csrf

    response = await call_next(request)

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
    message = "Das haben wir nicht gefunden." if exc.status_code == 404 else str(exc.detail)
    return _error_page(request, exc.status_code, message)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, _exc: RequestValidationError):
    return _error_page(request, 422, "Eingabe ungültig oder zu lang.")


@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    return templates.TemplateResponse(request, "landing.html", {})


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    # Muss unter "/" ausgeliefert werden, damit der Worker alle Seiten (/s/…) abdecken darf.
    return FileResponse(
        BASE_DIR / "static" / "sw.js",
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
