import os
import tempfile
from pathlib import Path

# Muss vor dem Import der App passieren (Engine wird beim Import erzeugt).
_TMP = Path(tempfile.mkdtemp(prefix="mampfplan-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["SPACE_TOKEN"] = "test-token-0123456789abcdefghij"
os.environ["MEMBERS"] = "Anna,Ben"
os.environ["SPOONACULAR_API_KEY"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

TOKEN = os.environ["SPACE_TOKEN"]
BASE = f"/s/{TOKEN}"


class Browser:
    """Dünner Wrapper um den TestClient, der CSRF-Token und Profil wie ein Browser mitschickt."""

    def __init__(self, client: TestClient):
        self.client = client
        self.base = BASE

    def get(self, path: str, **kwargs):
        return self.client.get(self.base + path, follow_redirects=False, **kwargs)

    def post(self, path: str, data: dict | None = None, htmx: bool = False, csrf: bool = True):
        headers = {}
        if csrf:
            headers["X-CSRF-Token"] = self.client.cookies.get("mp_csrf", "")
        if htmx:
            headers["HX-Request"] = "true"
        return self.client.post(self.base + path, data=data or {}, headers=headers, follow_redirects=False)

    def login(self, name: str = "Anna"):
        self.get("/who")  # stellt das CSRF-Cookie aus
        page = self.get("/who").text
        member_id = {"Anna": 1, "Ben": 2}[name]
        assert f'value="{member_id}"' in page
        response = self.post("/who", {"member_id": member_id})
        assert response.status_code == 303


@pytest.fixture(autouse=True)
def _clean_source_caches():
    """Die Rezeptquellen merken sich Ergebnisse 10 Minuten – das darf Tests nicht beeinflussen."""
    from app.recipe_sources import get_sources

    for source in get_sources().values():
        source._cache.clear()
    yield


@pytest.fixture
def anon():
    Base.metadata.drop_all(engine)
    with TestClient(app) as client:
        yield Browser(client)


@pytest.fixture
def web(anon):
    anon.login()
    return anon
