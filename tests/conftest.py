import os
import re
import tempfile
from pathlib import Path

# Must happen before importing the app (the engine is created on import).
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
    """Thin wrapper around the TestClient that sends the CSRF token and profile like a browser."""

    def __init__(self, client: TestClient, base: str = BASE):
        self.client = client
        self.base = base

    def household(self, token: str) -> "Browser":
        """The same browser (same cookies), but in the household with this link."""
        return Browser(self.client, f"/s/{token}")

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
        self.get("/who")  # issues the CSRF cookie
        page = self.get("/who").text
        match = re.search(rf'name="member_id" value="(\d+)">\s*<button[^>]*>{re.escape(name)}</button>', page)
        assert match, f"Profil {name} nicht auf der Profilauswahl"
        response = self.post("/who", {"member_id": int(match[1])})
        assert response.status_code == 303
        return self


@pytest.fixture(autouse=True)
def _clean_source_caches():
    """The recipe sources cache results for 10 minutes; that must not affect tests."""
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
