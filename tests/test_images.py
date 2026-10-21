import re
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

from app.config import Settings
from app.recipe_sources.base import ExternalIngredient, ExternalRecipe
from app.recipe_sources.chefkoch import ChefkochSource
from app.recipe_sources.mealdb import MealDBSource
from app.services import images
from app.services.images import ImageCache, cached_image_src, is_allowed_url, sniff_extension
from tests.conftest import BASE
from tests.test_plan import make_recipe

JPEG = b"\xff\xd8\xff\xe0" + b"j" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 200
CHEFKOCH_IMG = "https://img.chefkoch-cdn.de/rezepte/1/bilder/2/crop-360x240/lasagne.jpg"
MEALDB_IMG = "https://www.themealdb.com/images/media/meals/abc.jpg"


class Upstream:
    """Gefälschter Bildserver: zählt Anfragen und liefert konfigurierbare Antworten."""

    def __init__(self, response=None):
        self.calls: list[str] = []
        self.response = response or (lambda request: httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"}))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        return self.response(request)


def make_cache(tmp_path, upstream: Upstream, max_bytes: int = 10_000_000) -> ImageCache:
    return ImageCache(tmp_path / "cache", httpx.Client(transport=httpx.MockTransport(upstream)), max_bytes)


@pytest.fixture
def upstream(tmp_path, monkeypatch):
    fake = Upstream()
    cache = make_cache(tmp_path, fake)
    monkeypatch.setattr(images, "get_image_cache", lambda: cache)
    fake.cache = cache
    return fake


# --- Erlaubte Adressen ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [CHEFKOCH_IMG, MEALDB_IMG, "https://themealdb.com/x.png", "https://img.spoonacular.com/recipes/1-556x370.jpg"],
)
def test_allowed_urls(url):
    assert is_allowed_url(url)


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "http://img.chefkoch-cdn.de/a.jpg",  # kein https
        "https://evil.example/a.jpg",
        "https://img.chefkoch-cdn.de.evil.example/a.jpg",  # Namenstrick
        "https://evil.example/?u=https://img.chefkoch-cdn.de/a.jpg",
        "https://img.chefkoch-cdn.de@evil.example/a.jpg",  # Zugangsdaten-Trick
        "https://user:pw@img.chefkoch-cdn.de/a.jpg",
        "https://img.chefkoch-cdn.de:8443/a.jpg",  # fremder Port
        "https://127.0.0.1/a.jpg",
        "https://localhost/a.jpg",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://img.chefkoch-cdn.de/" + "a" * 600,
    ],
)
def test_disallowed_urls(url):
    assert not is_allowed_url(url)


def test_sniff_by_content_not_by_header():
    assert sniff_extension(JPEG) == "jpg"
    assert sniff_extension(PNG) == "png"
    assert sniff_extension(b"GIF89a....") == "gif"
    assert sniff_extension(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert sniff_extension(b"<svg onload=alert(1)></svg>") is None
    assert sniff_extension(b"<html>nope</html>") is None


# --- Cache-Dienst --------------------------------------------------------------------------


def test_downloads_once_then_serves_from_cache(tmp_path):
    fake = Upstream()
    cache = make_cache(tmp_path, fake)
    first = cache.get(CHEFKOCH_IMG)
    second = cache.get(CHEFKOCH_IMG)
    assert first and second and first == second
    assert first[1] == "image/jpeg" and first[0].read_bytes() == JPEG
    assert len(fake.calls) == 1  # zweiter Zugriff ohne Netzwerk


def test_cache_survives_restart(tmp_path):
    make_cache(tmp_path, Upstream()).get(CHEFKOCH_IMG)
    offline = Upstream(lambda request: httpx.Response(500))
    again = make_cache(tmp_path, offline)  # „neuer Prozess“, selbes Verzeichnis
    assert again.get(CHEFKOCH_IMG)[1] == "image/jpeg"
    assert offline.calls == []


def test_png_is_stored_with_matching_type(tmp_path):
    cache = make_cache(tmp_path, Upstream(lambda r: httpx.Response(200, content=PNG, headers={"content-type": "text/plain"})))
    path, mime = cache.get(MEALDB_IMG)
    assert mime == "image/png" and path.suffix == ".png"


def test_disallowed_url_never_hits_the_network(tmp_path):
    fake = Upstream()
    cache = make_cache(tmp_path, fake)
    assert cache.get("https://evil.example/a.jpg") is None
    assert cache.get("http://img.chefkoch-cdn.de/a.jpg") is None
    assert fake.calls == []


@pytest.mark.parametrize(
    "response",
    [
        lambda r: httpx.Response(404),
        lambda r: httpx.Response(500),
        lambda r: httpx.Response(200, content=b"<html>kein Bild</html>", headers={"content-type": "image/jpeg"}),
        lambda r: httpx.Response(200, content=b"<svg onload=alert(1)/>", headers={"content-type": "image/svg+xml"}),
        lambda r: httpx.Response(200, content=JPEG + b"x" * (images.MAX_IMAGE_BYTES + 1)),  # zu groß
        lambda r: httpx.Response(302, headers={"location": "https://evil.example/a.jpg"}),  # Weiterleitung
        lambda r: httpx.Response(302, headers={"location": MEALDB_IMG}),  # auch zu erlaubten Servern: nicht folgen
    ],
    ids=["404", "500", "html", "svg", "zu-gross", "redirect-fremd", "redirect-erlaubt"],
)
def test_bad_upstream_responses_are_rejected_and_not_stored(tmp_path, response):
    cache = make_cache(tmp_path, Upstream(response))
    assert cache.get(CHEFKOCH_IMG) is None
    assert list((tmp_path / "cache").iterdir()) == []


def test_network_errors_are_handled(tmp_path):
    def boom(request):
        raise httpx.ConnectError("kein Netz")

    cache = make_cache(tmp_path, Upstream(boom))
    assert cache.get(CHEFKOCH_IMG) is None


def test_failures_are_remembered_briefly(tmp_path):
    fake = Upstream(lambda r: httpx.Response(404))
    cache = make_cache(tmp_path, fake)
    for _ in range(5):
        assert cache.get(CHEFKOCH_IMG) is None
    assert len(fake.calls) == 1  # nicht bei jedem Seitenaufruf erneut versuchen


def test_recovers_after_failure_window(tmp_path, monkeypatch):
    state = {"ok": False}
    fake = Upstream(lambda r: httpx.Response(200, content=JPEG) if state["ok"] else httpx.Response(500))
    cache = make_cache(tmp_path, fake)
    assert cache.get(CHEFKOCH_IMG) is None
    state["ok"] = True
    cache._failures.clear()  # Zeitfenster abgelaufen
    assert cache.get(CHEFKOCH_IMG) is not None


def test_prune_removes_oldest_files_first(tmp_path):
    import os
    import time

    cache = make_cache(tmp_path, Upstream(), max_bytes=450)
    now = time.time()
    for index, name in enumerate(("alt", "mittel", "neu")):
        path = cache.directory / f"{name}.jpg"
        path.write_bytes(b"x" * 200)
        os.utime(path, (now - 100 + index, now - 100 + index))
    assert cache.prune() == 1
    assert sorted(p.stem for p in cache.directory.iterdir()) == ["mittel", "neu"]


def test_cache_dir_defaults_next_to_database():
    assert Settings(database_url="sqlite:////data/mampfplan.db").image_cache_path == Path("/data/imagecache")
    assert Settings(database_url="sqlite:////data/x.db", image_cache_dir="/tmp/bilder").image_cache_path == Path("/tmp/bilder")


# --- Endpunkt ------------------------------------------------------------------------------


def img_path(url: str) -> str:
    return f"/img?u={quote(url, safe='')}"


def test_endpoint_serves_cached_image_with_safe_headers(web, upstream):
    response = web.get(img_path(CHEFKOCH_IMG))
    assert response.status_code == 200 and response.content == JPEG
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "immutable" in response.headers["cache-control"] and "private" in response.headers["cache-control"]
    web.get(img_path(CHEFKOCH_IMG))
    assert len(upstream.calls) == 1  # beim zweiten Mal aus dem Cache


def test_endpoint_works_without_profile_but_needs_the_secret_token(anon, upstream):
    assert anon.get(img_path(CHEFKOCH_IMG)).status_code == 200  # <img> schickt keine Zusatzinfos; Token im Pfad genügt
    assert anon.client.get(f"/s/falsch/img?u={quote(CHEFKOCH_IMG, safe='')}").status_code == 404


@pytest.mark.parametrize("url", ["https://evil.example/a.jpg", "http://img.chefkoch-cdn.de/a.jpg", "file:///etc/passwd"])
def test_endpoint_refuses_foreign_urls_without_fetching(web, upstream, url):
    assert web.get(img_path(url)).status_code == 404
    assert upstream.calls == []


def test_endpoint_404_when_source_fails(web, upstream):
    upstream.response = lambda r: httpx.Response(500)
    assert web.get(img_path(CHEFKOCH_IMG)).status_code == 404


def test_endpoint_requires_a_url(web, upstream):
    assert web.get("/img").status_code == 422


# --- Einbindung in die Seiten ----------------------------------------------------------------


def external(image=CHEFKOCH_IMG, source="chefkoch"):
    return ExternalRecipe(
        source=source,
        external_id="1",
        title="Lasagne",
        image_url=image,
        source_url=f"https://www.chefkoch.de/rezepte/1/{source}.html",
        steps="Schichten.",
        ingredients=[ExternalIngredient("Nudeln", 500.0, "g")],
    )


def test_search_results_and_preview_use_the_local_cache_only(web, upstream, monkeypatch):
    monkeypatch.setattr(ChefkochSource, "_search", lambda self, q: [external()])
    monkeypatch.setattr(ChefkochSource, "_get", lambda self, i: external())
    results = web.get("/search?source=chefkoch&q=lasagne").text
    preview = web.get("/search/preview?source=chefkoch&id=1").text
    for page in (results, preview):
        assert f'src="{BASE}/img?u={quote(CHEFKOCH_IMG, safe="")}"' in page
        assert "https://img.chefkoch-cdn.de" not in page  # die Originaladresse steht nur URL-codiert im Link zum eigenen Server


def test_no_page_references_a_foreign_image_host(web, upstream, monkeypatch):
    monkeypatch.setattr(MealDBSource, "_get", lambda self, i: external(MEALDB_IMG, "mealdb"))
    location = web.post("/search/import", {"source": "mealdb", "external_id": "1", "day": "2026-10-21"}).headers["location"]
    recipe_id = location.split("/recipes/")[1].split("?")[0]
    web.post("/plan/recipe", {"day": "2026-10-21", "recipe_id": recipe_id})
    pages = [
        web.get(f"/recipes/{recipe_id}"),
        web.get("/recipes"),
        web.get("/plan?week=2026-10-21"),
        web.get("/plan/pick?day=2026-10-21"),
    ]
    for page in pages:
        assert page.status_code == 200
        assert re.findall(r'src="(https?://[^"]+)"', page.text) == []  # nur eigene (relative) Bildadressen
        assert f"{BASE}/img?u=" in page.text


def test_csp_blocks_images_from_foreign_hosts(web):
    csp = web.get("/plan").headers["content-security-policy"]
    img_src = re.search(r"img-src ([^;]+)", csp).group(1)
    assert img_src == "'self' data:" and "https:" not in img_src


def test_import_warms_the_cache(web, upstream, monkeypatch):
    monkeypatch.setattr(MealDBSource, "_get", lambda self, i: external(MEALDB_IMG, "mealdb"))
    assert web.post("/search/import", {"source": "mealdb", "external_id": "1"}).status_code == 303
    assert upstream.calls == [MEALDB_IMG]
    assert upstream.cache.lookup(MEALDB_IMG) is not None


def test_import_still_works_when_the_image_cannot_be_loaded(web, upstream, monkeypatch):
    upstream.response = lambda r: httpx.Response(500)
    monkeypatch.setattr(MealDBSource, "_get", lambda self, i: external(MEALDB_IMG, "mealdb"))
    response = web.post("/search/import", {"source": "mealdb", "external_id": "1"})
    assert response.status_code == 303
    assert web.get(response.headers["location"].removeprefix(BASE)).status_code == 200


def test_import_survives_unexpected_cache_errors(web, monkeypatch):
    def broken():
        raise RuntimeError("Platte voll")

    monkeypatch.setattr(images, "get_image_cache", broken)
    monkeypatch.setattr(MealDBSource, "_get", lambda self, i: external(MEALDB_IMG, "mealdb"))
    assert web.post("/search/import", {"source": "mealdb", "external_id": "1"}).status_code == 303


def test_recipe_form_has_no_free_image_url_field(web):
    assert 'name="image_url"' not in web.get("/recipes/new").text


def test_editing_an_imported_recipe_keeps_its_image(web, upstream, monkeypatch):
    monkeypatch.setattr(MealDBSource, "_get", lambda self, i: external(MEALDB_IMG, "mealdb"))
    location = web.post("/search/import", {"source": "mealdb", "external_id": "1"}).headers["location"]
    recipe_id = location.rsplit("/", 1)[1]
    web.post(f"/recipes/{recipe_id}/edit", {"title": "Lasagne neu", "ingredients": "500 g Nudeln", "steps": "x"})
    assert "/img?u=" in web.get(f"/recipes/{recipe_id}").text


def test_manual_recipes_have_no_image(web, upstream):
    recipe_id = make_recipe(web, "Pasta")
    assert "/img?u=" not in web.get(f"/recipes/{recipe_id}").text


def test_filter_builds_local_urls_only_for_allowed_hosts():
    assert cached_image_src(CHEFKOCH_IMG, "/s/t") == f"/s/t/img?u={quote(CHEFKOCH_IMG, safe='')}"
    assert cached_image_src("https://evil.example/a.jpg", "/s/t") == ""
    assert cached_image_src(None, "/s/t") == ""


def test_service_worker_caches_recipe_images(anon):
    sw = anon.client.get("/sw.js").text
    assert "mampfplan-images" in sw and 'endsWith("/img")' in sw
