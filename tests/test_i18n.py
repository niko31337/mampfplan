"""Mehrsprachigkeit: Sprachwahl, Übersetzungen, Katalog, Verlauf, Datum/Zahlen, Einheiten und Konverter."""

import html
import json
import re
from datetime import date

import pytest
from sqlalchemy import select

from app import i18n
from app.converter.text import parse_text
from app.db import SessionLocal
from app.i18n.extract import missing, msgids
from app.models import ActivityLog, ShoppingItem, Space
from app.services import shopping
from app.services.ingredients import format_amount, parse_line
from app.services.units import metric_line
from tests.conftest import BASE

GERMAN_MARKERS = ["Einkaufsliste", "Wochenplan", "Rückgängig", "Zubereitung", "Speichern", "Abbrechen", "Hauptnavigation"]
PAGES = [
    "/plan", "/recipes", "/recipes/new", "/recipes/convert", "/search?source=mealdb", "/shopping", "/shopping/catalog",
    "/shopping/categories", "/more", "/history", "/stats", "/households", "/who",
]  # fmt: skip


@pytest.fixture
def lang():
    """Sprache für Code außerhalb eines Requests setzen (und danach zurücksetzen)."""
    tokens = []

    def use(code):
        tokens.append(i18n.set_language(code))

    yield use
    for token in reversed(tokens):
        i18n.reset_language(token)


def html_lang(page: str) -> str:
    return re.search(r'<html lang="([a-z]+)"', page)[1]


# --- Sprachwahl ----------------------------------------------------------------------------------


def test_default_is_german(web):
    page = web.get("/plan").text
    assert html_lang(page) == "de"
    assert "Wochenplan – Mampfplan" in page


@pytest.mark.parametrize(
    "header, expected",
    [
        ("fr-FR,fr;q=0.9,en;q=0.8", "fr"),
        ("es", "es"),
        ("en-GB,en;q=0.9", "en"),
        ("de;q=0.5, es;q=0.9", "es"),
        ("it-IT,it;q=0.9", "de"),  # nicht unterstützt → Deutsch
        ("x" * 50, "de"),
    ],
)
def test_browser_language(web, header, expected):
    page = web.get("/plan", headers={"Accept-Language": header}).text
    assert html_lang(page) == expected


def test_cookie_beats_browser_language(web):
    web.client.cookies.set("mp_lang", "es")
    assert html_lang(web.get("/plan", headers={"Accept-Language": "fr"}).text) == "es"


def test_switching_language(web):
    token = web.client.cookies.get("mp_csrf")
    response = web.client.post("/lang", data={"lang": "fr", "next": f"{BASE}/more", "csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"{BASE}/more"
    assert "mp_lang=fr" in response.headers["set-cookie"]
    page = web.get("/more").text
    assert html_lang(page) == "fr" and "Langue" in page


@pytest.mark.parametrize("target", ["https://evil.example/", "//evil.example/x", "/\\evil.example", "plan"])
def test_switching_never_redirects_elsewhere(web, target):
    token = web.client.cookies.get("mp_csrf")
    response = web.client.post("/lang", data={"lang": "en", "next": target, "csrf_token": token}, follow_redirects=False)
    assert response.headers["location"] == "/"


def test_switching_needs_csrf_and_a_known_language(web):
    assert web.client.post("/lang", data={"lang": "en", "next": "/"}, follow_redirects=False).status_code == 403
    token = web.client.cookies.get("mp_csrf")
    response = web.client.post("/lang", data={"lang": "xx", "next": "/", "csrf_token": token}, follow_redirects=False)
    assert "mp_lang" not in response.headers.get("set-cookie", "")


def test_language_picker_on_more_who_and_landing(web, anon):
    for page in (web.get("/more").text, web.get("/who").text, web.client.get("/").text):
        assert 'action="/lang"' in page
        for code in ("de", "en", "fr", "es"):
            assert f'value="{code}"' in page


# --- Alle Seiten in allen Sprachen -------------------------------------------------------------


@pytest.mark.parametrize("code", ["en", "fr", "es"])
def test_all_pages_are_translated(web, code):
    web.post("/recipes/new", {"title": "Pasta", "ingredients": "200 g Spaghetti", "steps": "Kochen."})
    web.post("/shopping/add", {"name": "Milch"})
    web.client.cookies.set("mp_lang", code)
    for path in PAGES:
        response = web.get(path)
        assert response.status_code == 200, path
        assert html_lang(response.text) == code, path
        visible = re.sub(r"<[^>]+>", " ", response.text)
        for marker in GERMAN_MARKERS:
            assert marker not in visible, (code, path, marker)


@pytest.mark.parametrize("code, expected", [("en", "Bought"), ("fr", "Acheté"), ("es", "Comprado")])
def test_js_texts_are_translated(web, code, expected):
    web.client.cookies.set("mp_lang", code)
    page = web.get("/shopping").text
    data = json.loads(re.search(r'<script type="application/json" id="i18n">(.*?)</script>', page, re.S)[1])
    assert data["Gekauft"] == expected
    assert data["Obst & Gemüse"] != "Obst & Gemüse"


def test_error_messages_are_translated(web):
    web.client.cookies.set("mp_lang", "en")
    page = html.unescape(web.get("/recipes/999999").text)
    assert "We couldn't find that." in page
    response = web.post("/recipes/convert", {"url": "http://blog.example/x"})
    assert "Only https:// links are supported." in response.text


# --- Übersetzungsdateien ------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["en", "fr", "es"])
def test_translation_files_are_complete(code):
    assert missing(code) == []
    data = json.loads((i18n.DIR / f"{code}.json").read_text(encoding="utf-8"))
    assert set(data) <= msgids(), set(data) - msgids()  # keine verwaisten Einträge
    for msgid, text in data.items():
        assert text.strip(), msgid
        assert sorted(re.findall(r"%\((\w+)\)s", msgid)) == sorted(re.findall(r"%\((\w+)\)s", text)), msgid


def test_missing_translation_falls_back_to_german(lang):
    lang("fr")
    assert i18n.gettext("Ein Text, den es nicht gibt") == "Ein Text, den es nicht gibt"


# --- Katalog und Kategorien -------------------------------------------------------------------


@pytest.mark.parametrize("code, query, expected", [("en", "mil", "Milk"), ("fr", "lai", "Lait"), ("es", "lech", "Leche"), ("de", "milc", "Milch")])
def test_catalog_suggestions_in_the_chosen_language(web, code, query, expected):
    web.client.cookies.set("mp_lang", code)
    page = web.get(f"/shopping/suggest?name={query}").text
    assert f">{expected}</button>" in page


def test_catalog_page_and_categories_are_translated(web):
    web.client.cookies.set("mp_lang", "fr")
    page = web.get("/shopping/catalog").text
    assert "Fruits &amp; légumes" in page and ">Pommes</button>" in page


@pytest.mark.parametrize("name", ["Milk", "Lait", "Leche", "Milch"])
def test_category_is_recognised_in_every_language(web, name):
    web.post("/shopping/add", {"name": name})
    with SessionLocal() as db:
        item = db.scalars(select(ShoppingItem).where(ShoppingItem.name == name)).one()
        assert item.category == "Milch, Käse & Eier"  # interner Schlüssel bleibt deutsch


def test_shopping_list_shows_translated_categories(web):
    web.post("/shopping/add", {"name": "Bananen"})
    web.client.cookies.set("mp_lang", "es")
    page = web.get("/shopping").text
    assert "<span>Frutas y verduras</span>" in page
    assert 'data-category="Obst &amp; Gemüse"' in page  # Daten weiter mit internem Schlüssel


# --- Verlauf ----------------------------------------------------------------------------------


def test_history_is_shown_in_the_readers_language(web):
    web.post("/shopping/add", {"name": "Brot"})  # auf Deutsch angelegt
    web.client.cookies.set("mp_lang", "en")
    page = web.get("/history").text
    assert "Added “Brot” to the shopping list" in page
    assert "Undo: Added “Brot” to the shopping list" in web.get("/plan").text


def test_history_entries_with_dates_and_categories(web, lang):
    from app.services.history import describe, msg

    stored = msg("„%(name)s“ nach „%(category)s“ verschoben", name="Feta", category="Milch, Käse & Eier")
    lang("fr")
    assert describe(stored) == "« Feta » déplacé vers « Produits laitiers & œufs »"
    stored = msg("„%(title)s“ für %(day)s eingeplant", title="Pasta", day=date(2026, 11, 3))
    assert describe(stored) == "« Pasta » planifié pour le 03/11"


def test_old_plain_text_history_stays_readable(web):
    with SessionLocal() as db:
        space = db.scalars(select(Space)).first()
        db.add(ActivityLog(space_id=space.id, member_name="Anna", description="Alter Eintrag von früher", undo_ops="[]"))
        db.commit()
    web.client.cookies.set("mp_lang", "en")
    assert "Alter Eintrag von früher" in web.get("/history").text


# --- Datum, Zahlen, API -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code, long, short",
    [
        ("de", "Dienstag, 3. November", "03.11."),
        ("en", "Tuesday, 3 November", "3 Nov"),
        ("fr", "mardi 3 novembre", "03/11"),
        ("es", "martes, 3 de noviembre", "03/11"),
    ],
)
def test_dates(lang, code, long, short):
    lang(code)
    assert i18n.date_long(date(2026, 11, 3)) == long
    assert i18n.date_short(date(2026, 11, 3)) == short


@pytest.mark.parametrize("code, expected", [("de", "1,5"), ("en", "1.5"), ("fr", "1,5"), ("es", "1,5")])
def test_decimal_separator(lang, code, expected):
    lang(code)
    assert format_amount(1.5) == expected


def test_week_view_uses_translated_weekdays(web):
    web.client.cookies.set("mp_lang", "es")
    page = web.get("/plan?week=2026-11-02").text
    assert "lunes, 2 de noviembre" in page and "Semana 45" in page


@pytest.mark.parametrize("code, monday", [("de", "Montag"), ("en", "Monday"), ("fr", "lundi"), ("es", "lunes")])
def test_api_weekday_language(anon, monkeypatch, code, monday):
    from app.routers import api

    monkeypatch.setattr(api, "today", lambda: date(2026, 11, 2))
    data = anon.get(f"/api/plan?lang={code}").json()
    assert data["days"][0]["weekday"] == monday


def test_api_defaults_to_german_and_rejects_unknown_languages(anon):
    assert anon.get("/api/plan", headers={"Accept-Language": "en"}).json()["days"][0]["weekday"] in i18n.WEEKDAYS["de"]
    assert anon.get("/api/plan?lang=it").status_code == 422


# --- Einheiten und Konverter ------------------------------------------------------------------


@pytest.mark.parametrize(
    "line, expected",
    [
        ("2 cuillères à soupe d'huile", (2.0, "cs", "huile")),
        ("1 cuillère à café de sel", (1.0, "cc", "sel")),
        ("1 pincée de sel", (1.0, "pincée", "sel")),
        ("3 gousses d'ail", (3.0, "gousses", "ail")),
        ("2 cucharadas de aceite", (2.0, "cucharadas", "aceite")),
        ("2 dientes de ajo", (2.0, "dientes", "ajo")),
        ("1 taza de arroz", (1.0, "taza", "arroz")),
        ("1 cup of flour", (1.0, "cup", "flour")),
        ("200 g Mehl", (200.0, "g", "Mehl")),
    ],
)
def test_french_and_spanish_units(line, expected):
    assert parse_line(line) == expected


@pytest.mark.parametrize("code, expected", [("de", "2 EL soy sauce"), ("en", "2 tbsp soy sauce"), ("fr", "2 cs soy sauce"), ("es", "2 cda soy sauce")])
def test_us_spoons_use_the_display_language(lang, code, expected):
    lang(code)
    assert metric_line("2 tbsp soy sauce") == expected


def test_french_recipe_caption():
    text = """Tarte aux pommes 🍎

Ingrédients pour 6 personnes :
- 200 g de farine
- 3 pommes

Préparation :
1. Mélanger la farine.
2. Cuire 35 minutes.

Abonnez-vous pour plus de recettes ! #tarte"""
    result = parse_text(text.splitlines())
    assert result.title == "Tarte aux pommes"
    assert result.servings == 6
    assert result.ingredients == ["200 g de farine", "3 pommes"]
    assert result.steps == ["Mélanger la farine.", "Cuire 35 minutes."]


def test_spanish_recipe_caption():
    text = """Tortilla de patatas

Ingredientes (para 4 personas):
• 6 huevos
• 500 g de patatas

Preparación:
1. Pelar las patatas.
2. Freír y mezclar con los huevos.

Sígueme para más recetas"""
    result = parse_text(text.splitlines())
    assert result.servings == 4
    assert result.ingredients == ["6 huevos", "500 g de patatas"]
    assert result.steps == ["Pelar las patatas.", "Freír y mezclar con los huevos."]


def test_search_starts_with_mealdb_outside_german(web):
    web.client.cookies.set("mp_lang", "en")
    page = web.get("/search").text
    assert re.search(r'class="tab is-active"[^>]*href="[^"]*source=mealdb', page)
    web.client.cookies.set("mp_lang", "de")
    assert re.search(r'class="tab is-active"[^>]*href="[^"]*source=chefkoch', web.get("/search").text)


def test_category_guess_service_directly():
    with SessionLocal() as db:
        assert shopping.guess_category(db, "Toothpaste") == "Hygiene & Drogerie"
        assert shopping.guess_category(db, "Papel higiénico") == "Haushalt & Reinigung"
