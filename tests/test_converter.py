"""Rezept-Konverter: Erkennung aus Rezept-Markup, Blogtext und Instagram-Beschreibungen; Seitenablauf."""

import html as html_lib
import json
import re
from html import escape
from urllib.parse import quote

import httpx
import pytest
from sqlalchemy import select

from app import converter
from app.converter import FetchError, convert, source_kind, with_source_line
from app.converter import fetch as fetching
from app.converter.fetch import Fetched
from app.converter.html import structured_recipe, text_lines
from app.converter.text import parse_text
from app.db import SessionLocal
from app.models import Recipe
from app.services import images
from app.services.images import ImageCache

JPEG = b"\xff\xd8\xff\xe0" + b"j" * 200

# --- Beispieltexte (selbst geschrieben, im Aufbau echten Posts nachempfunden) -------------------

CAPTION_DE = """Ofen-Gnocchi mit Feta 🧀🍅

Schnell, cremig und alles auf einem Blech!

Zutaten für 2 Portionen:
▫️ 500 g Gnocchi
▫️ 250 g Kirschtomaten, halbiert
▫️ 1 rote Zwiebel
▫️ 200 g Feta
▫️ Salz & Pfeffer

Zubereitung:
✅ Ofen auf 200 °C vorheizen
✅ Alles aufs Blech geben und 20 Minuten backen
✅ Feta zerdrücken, verrühren – fertig!

🌿 Perfekt für stressige Abende.

📌 Speicher dir das Rezept für später!
#gnocchi #ofengericht #schnelleküche"""

CAPTION_TWO_SECTIONS = """Zwetschgenkuchen

Zutaten für den Teig:
- 300 g Mehl
- 1 Ei

Zutaten für den Belag:
- 1 kg Zwetschgen

Zubereitung:
1. Teig kneten und ausrollen.
2. Mit Zwetschgen belegen und backen.

Zutaten (English):
- 300 g flour"""

CAPTION_EN_INLINE = """Crispy Rice Bowl 🍚🔥

Comment “BOWL” and I’ll send you the recipe card!

Ingredients: -2 cups cooked rice -1 tbsp sesame oil -2 eggs -1/2 cucumber, sliced
For Topping: -Sesame seeds -Spring onions, sliced

Instructions:
1. Press the rice into a hot pan and fry until crispy.
2. Fry the eggs.
3. Top everything and serve.

#ricebowl #easyrecipes"""

CAPTION_MIXED = """LINSEN DAL 🌶️
Mein Lieblingsessen im Winter ✨
REZEPT (für 3-4 Personen):
-1 Zwiebel
-2 Knoblauchzehen
In etwas Öl glasig dünsten.
-200 g rote Linsen
-400 ml Kokosmilch
Dazugeben und 15 Minuten köcheln.
Topping:
-frischer Koriander
Mit Reis servieren.
-
LINSEN-DAL
Mein Winteressen
REZEPT (for 3-4 people):
-1 onion
Sweat in some oil.
#dal #vegan"""

CAPTION_NO_STEPS = """ZITRONENKUCHEN

Zutaten

4 Eier
200 g Zucker
1 Bio-Zitrone
Backpapier

✨ Alle Rezepte findest du über den Link in der Bio! @kuchenliebe

#kuchen #backen"""

BLOG_HTML = """<html><head><title>Bohnen-Chili – Mein Kochblog</title>
<meta property="og:title" content="Bohnen-Chili - Mein Kochblog">
<meta property="og:image" content="https://blog.example/bilder/chili.jpg"></head>
<body><nav><a href="/rezepte">Rezepte</a></nav>
<article>
<p>Heute gibt es etwas Wärmendes für kalte Tage &#8211; mein liebstes Chili.</p>
<h1>Bohnen-Chili mit Mais</h1>
<h5>Zutaten</h5>
<p><i>Für vier Portionen</i></p>
<p>1 Zwiebel<br />
2 Dosen Kidneybohnen<br>
1 Dose Mais</p>
<p><i>Für die Gewürzmischung</i></p>
<p>1 TL Kreuzkümmel<br />
1 EL Paprikapulver</p>
<h5>Zubereitung</h5>
<p>Die Zwiebel würfeln und in einem Topf anbraten.</p>
<p>Bohnen, Mais und Gewürze dazugeben und 20 Minuten köcheln lassen.</p>
<p>&nbsp;</p>
<h5>Hier findest du noch mehr Rezepte</h5>
<ul><li><a href="/x">Linsensuppe</a></li></ul>
</article>
<script>var zutaten = "Zutaten: nicht mitnehmen";</script>
<footer>Impressum</footer>
</body></html>"""


def ld_page(recipe: dict, extra_head: str = "") -> str:
    return (
        "<html><head>"
        f'<meta property="og:image" content="https://cdn.example/og.jpg">{extra_head}'
        f'<script type="application/ld+json">{json.dumps(recipe, ensure_ascii=False)}</script>'
        "</head><body><h1>Seite</h1></body></html>"
    )


HELLO_RECIPE = {
    "@context": "http://schema.org/",
    "@type": "Recipe",
    "name": "Hähnchen mit Ofengemüse &amp; Kräutern",
    "image": "https://img.example/haehnchen.jpg",
    "recipeYield": 2,
    "recipeIngredient": ["300 g Hähnchenbrust", "½ Stück Knoblauchzehe", "ml Öl", "nach Geschmack Salz"],
    "recipeInstructions": [
        {"@type": "HowToStep", "text": "Ofen vorheizen.\nGemüse\nschneiden."},
        {"@type": "HowToStep", "text": "Alles 25 Min. backen."},
    ],
}


def instagram_page(caption: str) -> str:
    title = f'Koch Kanal auf Instagram: "{caption}"'
    return (
        "<html><head>"
        f'<meta property="og:title" content="{escape(title)}">'
        '<meta property="og:image" content="https://scontent.cdninstagram.example/v/bild.jpg?sig=abc">'
        "</head><body></body></html>"
    )


# --- Erkennung ----------------------------------------------------------------------------------


def test_german_caption_with_headings_and_emoji_bullets():
    result = parse_text(CAPTION_DE.splitlines())
    assert result.title == "Ofen-Gnocchi mit Feta"
    assert result.servings == 2
    assert result.ingredients == ["500 g Gnocchi", "250 g Kirschtomaten, halbiert", "1 rote Zwiebel", "200 g Feta", "Salz & Pfeffer"]
    # Plauderei und Hashtags nach der Aufzählung gehören nicht zur Anleitung
    assert result.steps == ["Ofen auf 200 °C vorheizen", "Alles aufs Blech geben und 20 Minuten backen", "Feta zerdrücken, verrühren – fertig!"]


def test_english_caption_with_inline_ingredients_and_numbered_steps():
    result = parse_text(CAPTION_EN_INLINE.splitlines())
    assert result.title == "Crispy Rice Bowl"
    assert result.ingredients == [
        "2 cups cooked rice",
        "1 tbsp sesame oil",
        "2 eggs",
        "1/2 cucumber, sliced",
        "Sesame seeds",
        "Spring onions, sliced",
    ]
    assert result.steps == ["Press the rice into a hot pan and fry until crispy.", "Fry the eggs.", "Top everything and serve."]


def test_caption_with_ingredients_and_steps_mixed_ends_before_a_translation():
    result = parse_text(CAPTION_MIXED.splitlines())
    assert result.title == "Linsen Dal"
    assert result.servings == 3
    assert result.ingredients == ["1 Zwiebel", "2 Knoblauchzehen", "200 g rote Linsen", "400 ml Kokosmilch", "frischer Koriander"]
    assert result.steps == ["In etwas Öl glasig dünsten.", "Dazugeben und 15 Minuten köcheln.", "Mit Reis servieren."]


def test_several_ingredient_sections_and_a_repeat_after_the_steps():
    result = parse_text(CAPTION_TWO_SECTIONS.splitlines())
    assert result.ingredients == ["300 g Mehl", "1 Ei", "1 kg Zwetschgen"]
    assert result.steps == ["Teig kneten und ausrollen.", "Mit Zwetschgen belegen und backen."]


def test_caption_without_steps_stops_before_promotion():
    result = parse_text(CAPTION_NO_STEPS.splitlines())
    assert result.title == "Zitronenkuchen"
    assert result.ingredients == ["4 Eier", "200 g Zucker", "1 Bio-Zitrone", "Backpapier"]
    assert result.steps == []


def test_text_without_recipe_finds_nothing():
    result = parse_text(["Schöner Tag am See 🌊", "", "#sommer"])
    assert result.ingredients == [] and result.steps == []


def test_blog_article_without_markup():
    result = parse_text(text_lines(BLOG_HTML))
    assert result.title == "Bohnen-Chili mit Mais"
    assert result.servings == 4
    assert result.ingredients == ["1 Zwiebel", "2 Dosen Kidneybohnen", "1 Dose Mais", "1 TL Kreuzkümmel", "1 EL Paprikapulver"]
    assert result.steps == [
        "Die Zwiebel würfeln und in einem Topf anbraten.",
        "Bohnen, Mais und Gewürze dazugeben und 20 Minuten köcheln lassen.",
    ]


def test_structured_recipe_from_json_ld():
    recipe = structured_recipe(ld_page(HELLO_RECIPE))
    assert recipe.title == "Hähnchen mit Ofengemüse & Kräutern"
    assert recipe.servings == 2
    assert recipe.image_url == "https://img.example/haehnchen.jpg"
    assert recipe.steps == ["Ofen vorheizen. Gemüse schneiden.", "Alles 25 Min. backen."]


@pytest.mark.parametrize(
    "variant",
    [
        {"@graph": [{"@type": "WebPage"}, {**HELLO_RECIPE, "@type": ["Recipe", "NewsArticle"]}]},
        [{"@type": "Organization"}, HELLO_RECIPE],
    ],
)
def test_json_ld_in_graph_or_list(variant):
    assert structured_recipe(ld_page(variant)).title.startswith("Hähnchen")


def test_json_ld_sections_strings_and_yield_text():
    recipe = structured_recipe(
        ld_page(
            {
                "@type": "Recipe",
                "name": "Pizza",
                "recipeYield": ["4", "4 Stück"],
                "image": [{"@type": "ImageObject", "url": "https://img.example/pizza.jpg"}],
                "recipeIngredient": ["500 g Mehl"],
                "recipeInstructions": [
                    {"@type": "HowToSection", "name": "Teig", "itemListElement": [{"@type": "HowToStep", "text": "Kneten."}]},
                    {"@type": "HowToSection", "name": "Belag", "itemListElement": [{"@type": "HowToStep", "text": "Belegen."}]},
                ],
            }
        )
    )
    assert recipe.servings == 4
    assert recipe.image_url == "https://img.example/pizza.jpg"
    assert recipe.steps == ["Teig:", "Kneten.", "Belag:", "Belegen."]
    assert structured_recipe(ld_page({"@type": "Recipe", "name": "X", "recipeInstructions": "Erst das.\nDann das."})).steps == [
        "Erst das.",
        "Dann das.",
    ]


def test_broken_json_ld_is_ignored():
    page = '<script type="application/ld+json">{kaputt</script>' + ld_page(HELLO_RECIPE)
    assert structured_recipe(page).title.startswith("Hähnchen")
    assert structured_recipe("<html></html>") is None


@pytest.mark.parametrize(
    "url, kind",
    [
        ("https://www.instagram.com/p/abc/", "instagram"),
        ("https://instagram.com/reel/abc/", "instagram"),
        ("https://www.hellofresh.de/recipes/x", "hellofresh"),
        ("https://www.hellofresh.co.uk/recipes/x", "hellofresh"),
        ("https://instagram.com.evil.example/p/abc", "link"),
        ("https://blog.example/rezept", "link"),
    ],
)
def test_source_kind(url, kind):
    assert source_kind(url) == kind


def test_source_line_is_appended_once():
    url = "https://blog.example/rezept"
    assert with_source_line("1. Kochen", url) == f"1. Kochen\n\nQuelle: {url}"
    assert with_source_line(f"1. Kochen\n\nQuelle: {url}", url) == f"1. Kochen\n\nQuelle: {url}"
    assert with_source_line("", url) == f"Quelle: {url}"
    assert with_source_line("1. Kochen", None) == "1. Kochen"


# --- convert() mit gefälschtem Abruf ----------------------------------------------------------


class Table(dict):
    """URL → HTML; merkt sich zusätzlich die abgerufenen Adressen (requested)."""


@pytest.fixture
def pages(monkeypatch):
    """Gefälschtes Internet: URL → HTML (oder FetchError). Bilder liefern ein JPEG."""
    table = Table()
    requested: list[str] = []

    def fake_fetch(url, *, accept=fetching.HTML_TYPES, max_bytes=0, client=None):
        url = fetching.validate_url(url)
        requested.append(url)
        if accept != fetching.HTML_TYPES:
            return Fetched(url, JPEG, "image/jpeg", None)
        page = table.get(url)
        if page is None:
            raise FetchError("Die Seite antwortet mit Fehler 404.")
        if isinstance(page, Exception):
            raise page
        return Fetched(url, page.encode(), "text/html", "utf-8")

    monkeypatch.setattr(fetching, "fetch", fake_fetch)
    table.requested = requested  # type: ignore[attr-defined]
    return table


def test_convert_structured_page(pages):
    pages["https://www.hellofresh.de/recipes/haehnchen"] = ld_page(HELLO_RECIPE)
    draft = convert("https://www.hellofresh.de/recipes/haehnchen")
    assert draft.source == "hellofresh"
    assert draft.title == "Hähnchen mit Ofengemüse & Kräutern"
    assert draft.ingredient_text == "300 g Hähnchenbrust\n½ Stück Knoblauchzehe\nÖl\nSalz"
    assert draft.steps_text == (
        "1. Ofen vorheizen. Gemüse schneiden.\n\n2. Alles 25 Min. backen.\n\nQuelle: https://www.hellofresh.de/recipes/haehnchen"
    )
    assert draft.image_url == "https://img.example/haehnchen.jpg"


def test_convert_blog_uses_text_and_og_image(pages):
    pages["https://blog.example/chili"] = BLOG_HTML
    draft = convert("https://blog.example/chili")
    assert draft.source == "link" and draft.found
    assert draft.title == "Bohnen-Chili mit Mais"
    assert draft.image_url == "https://blog.example/bilder/chili.jpg"
    assert draft.steps_text.endswith("Quelle: https://blog.example/chili")


def test_convert_page_without_recipe_keeps_the_title(pages):
    pages["https://blog.example/news"] = '<html><head><meta property="og:title" content="Neuigkeiten | Blog"><meta property="og:site_name" content="Blog"></head><body><p>Nichts zu essen.</p></body></html>'
    draft = convert("https://blog.example/news")
    assert not draft.found
    assert draft.title == "Neuigkeiten"


def test_convert_instagram_caption_from_meta(pages):
    pages["https://www.instagram.com/p/abc/"] = instagram_page(CAPTION_DE)
    draft = convert("https://www.instagram.com/p/abc/")
    assert draft.source == "instagram" and not draft.needs_text
    assert draft.title == "Ofen-Gnocchi mit Feta"
    assert "500 g Gnocchi" in draft.ingredient_text


@pytest.mark.parametrize("page", ["<html><head><title>Instagram</title></head><body></body></html>", FetchError("Fehler 429")])
def test_instagram_without_caption_asks_for_the_text(pages, page):
    pages["https://www.instagram.com/p/abc/"] = page
    draft = convert("https://www.instagram.com/p/abc/")
    assert draft.needs_text and not draft.found


def test_pasted_text_is_used_without_fetching(pages):
    draft = convert("https://www.instagram.com/p/abc/", CAPTION_MIXED)
    assert pages.requested == []
    assert draft.title == "Linsen Dal" and draft.source_url == "https://www.instagram.com/p/abc/"


def test_other_fetch_errors_are_reported(pages):
    with pytest.raises(FetchError, match="404"):
        convert("https://blog.example/fehlt")
    with pytest.raises(FetchError, match="https"):
        convert("http://blog.example/unsicher")


# --- Seitenablauf ------------------------------------------------------------------------------


@pytest.fixture
def image_cache(tmp_path, monkeypatch):
    cache = ImageCache(tmp_path / "cache", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))), 10_000_000)
    monkeypatch.setattr(images, "get_image_cache", lambda: cache)
    return cache


def convert_page(web, url, text=""):
    return web.post("/recipes/convert", {"url": url, "text": text})


def hidden(page: str, name: str) -> str:
    match = re.search(rf'name="{name}" value="([^"]*)"', page)
    return match[1] if match else ""


def save_draft(web, page: str, **changes):
    fields = {
        "title": re.search(r'name="title" value="([^"]*)"', page)[1],
        "servings": re.search(r'name="servings"[^>]*value="([^"]*)"', page)[1],
        "ingredients": re.search(r'name="ingredients"[^>]*>([^<]*)</textarea>', page)[1],
        "steps": re.search(r'name="steps"[^>]*>([^<]*)</textarea>', page)[1],
        "source": hidden(page, "source"),
        "source_url": hidden(page, "source_url"),
        "image_url": hidden(page, "image_url"),
    }
    fields = {k: html_lib.unescape(v) for k, v in fields.items()}
    fields.update(changes)
    response = web.post("/recipes/new", fields)
    assert response.status_code == 303
    recipe_id = int(response.headers["location"].rsplit("/", 1)[1])
    with SessionLocal() as db:
        recipe = db.get(Recipe, recipe_id)
        db.expunge(recipe)
        return recipe


def test_recipes_page_links_to_the_converter(web):
    assert f'href="{web.base}/recipes/convert"' in web.get("/recipes").text
    page = web.get("/recipes/convert?url=https://blog.example/x").text
    assert 'value="https://blog.example/x"' in page


def test_converter_needs_a_profile(anon):
    assert anon.get("/recipes/convert").status_code == 303


def test_preview_is_editable_and_saved_with_source(web, pages, image_cache):
    url = "https://blog.example/chili"
    pages[url] = BLOG_HTML
    response = convert_page(web, url)
    assert response.status_code == 200
    page = response.text
    assert "Vorschlag aus Link" in page
    assert 'value="Bohnen-Chili mit Mais"' in page
    assert "2 Dosen Kidneybohnen" in page
    assert f"Quelle: {url}" in page
    with SessionLocal() as db:
        assert db.scalars(select(Recipe)).all() == []  # Vorschau speichert nichts

    # Anpassen: Titel ändern, Zutat ergänzen, Quellenzeile löschen → kommt beim Speichern zurück
    recipe = save_draft(web, page, title="Chili für Zuhause", ingredients="1 Zwiebel\n1 Paprika", steps="1. Alles kochen.")
    assert recipe.title == "Chili für Zuhause"
    assert recipe.source == "link" and recipe.source_url == url
    assert recipe.steps == f"1. Alles kochen.\n\nQuelle: {url}"
    assert recipe.servings == 4

    detail = web.get(f"/recipes/{recipe.id}").text
    assert f'<a href="{url}" rel="noopener noreferrer nofollow" target="_blank">{url}</a>' in detail
    assert "Quelle: blog.example" in detail


def test_source_line_stays_when_editing(web, pages, image_cache):
    url = "https://www.hellofresh.de/recipes/haehnchen"
    pages[url] = ld_page(HELLO_RECIPE)
    recipe = save_draft(web, convert_page(web, url).text)
    web.post(f"/recipes/{recipe.id}/edit", {"title": recipe.title, "ingredients": "Hähnchen", "steps": "Nur noch backen."})
    with SessionLocal() as db:
        assert db.get(Recipe, recipe.id).steps == f"Nur noch backen.\n\nQuelle: {url}"


def test_manual_recipes_get_no_source_line(web):
    response = web.post("/recipes/new", {"title": "Eigenes", "steps": "Kochen.", "source": "manual"})
    recipe_id = int(response.headers["location"].rsplit("/", 1)[1])
    with SessionLocal() as db:
        recipe = db.get(Recipe, recipe_id)
        assert recipe.steps == "Kochen." and recipe.source == "manual" and recipe.source_url is None


@pytest.mark.parametrize(
    "source, source_url",
    [("chefkoch", "https://blog.example/x"), ("link", "javascript:alert(1)"), ("link", "http://blog.example/x"), ("link", "")],
)
def test_forged_source_fields_are_ignored(web, source, source_url):
    response = web.post("/recipes/new", {"title": "X", "steps": "Y", "source": source, "source_url": source_url, "image_url": "https://evil.example/a.jpg"})
    recipe_id = int(response.headers["location"].rsplit("/", 1)[1])
    with SessionLocal() as db:
        recipe = db.get(Recipe, recipe_id)
        assert (recipe.source, recipe.source_url, recipe.image_url, recipe.steps) == ("manual", None, None, "Y")


def test_image_is_stored_and_served_from_the_cache(web, pages, image_cache):
    url = "https://www.instagram.com/p/abc/"
    pages[url] = instagram_page(CAPTION_DE)
    page = convert_page(web, url).text
    key = "https://scontent.cdninstagram.example/v/bild.jpg?sig=abc"
    assert hidden(page, "image_url") == key
    src = f"{web.base}/img?u={quote(key, safe='')}"
    assert f'src="{src}"' in page
    image = web.client.get(src)
    assert image.status_code == 200 and image.content == JPEG

    recipe = save_draft(web, page)
    assert recipe.image_url == key
    assert f'src="{src}"' in web.get(f"/recipes/{recipe.id}").text


def test_foreign_image_is_never_downloaded_by_the_image_route(web, image_cache):
    assert web.client.get(f"{web.base}/img?u={quote('https://evil.example/a.jpg', safe='')}").status_code == 404


def test_long_signed_image_urls_are_stored_without_query():
    long = "https://cdn.example/bild.jpg?" + "x" * 600
    assert converter.image_key(long) == "https://cdn.example/bild.jpg"
    assert converter.image_key("http://cdn.example/a.jpg") is None


def test_instagram_without_caption_shows_the_paste_box(web, pages):
    url = "https://www.instagram.com/p/abc/"
    pages[url] = "<html><head><title>Instagram</title></head><body></body></html>"
    page = convert_page(web, url).text
    assert "kopiere die Beschreibung" in page
    assert f'value="{url}"' in page
    assert re.search(r"<details\s+open>", page)

    preview = convert_page(web, url, CAPTION_EN_INLINE).text
    assert 'value="Crispy Rice Bowl"' in preview
    assert "Quelle: https://www.instagram.com/p/abc/" in preview


def test_invalid_link_shows_an_error(web, pages):
    response = convert_page(web, "http://blog.example/x")
    assert response.status_code == 422
    assert "Nur Links mit https://" in response.text


def test_duplicate_is_pointed_out(web, pages, image_cache):
    url = "https://blog.example/chili"
    pages[url] = BLOG_HTML
    recipe = save_draft(web, convert_page(web, url).text)
    page = convert_page(web, url).text
    assert "Dieses Rezept hast du schon" in page
    assert f"/recipes/{recipe.id}" in page


def test_preview_without_recipe_content_hints_at_pasting(web, pages):
    url = "https://blog.example/news"
    pages[url] = "<html><head><title>Neuigkeiten</title></head><body><p>Nichts zu essen.</p></body></html>"
    page = convert_page(web, url).text
    assert "keine Zutaten oder Schritte erkannt" in page
    assert 'value="Neuigkeiten"' in page
