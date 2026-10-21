import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

USER_AGENT = "Mampfplan/1.0 (private family app)"
TIMEOUT = 8.0
CACHE_SECONDS = 600
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class SourceError(Exception):
    """Quelle nicht erreichbar oder liefert unerwartete Daten."""


@dataclass
class ExternalIngredient:
    name: str
    amount: float | None = None
    unit: str = ""


@dataclass
class ExternalRecipe:
    source: str
    external_id: str
    title: str
    image_url: str | None = None
    source_url: str | None = None
    servings: int | None = None
    steps: str = ""
    ingredients: list[ExternalIngredient] = field(default_factory=list)


def safe_https_url(url: str | None) -> str | None:
    """Nur https-URLs übernehmen (verhindert javascript:- und data:-URLs in href/src)."""
    if url and url.startswith("https://") and len(url) <= 500:
        return url
    return None


class RecipeSource(ABC):
    key: str
    label: str
    language: str

    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        self._cache: dict[tuple, tuple[float, object]] = {}

    @property
    def enabled(self) -> bool:
        return True

    @abstractmethod
    def _search(self, query: str) -> list[ExternalRecipe]: ...

    @abstractmethod
    def _get(self, external_id: str) -> ExternalRecipe: ...

    def search(self, query: str) -> list[ExternalRecipe]:
        return self._cached(("search", query.casefold()), lambda: self._search(query))

    def get(self, external_id: str) -> ExternalRecipe:
        if not ID_PATTERN.match(external_id):
            raise SourceError("Ungültige Rezept-ID")
        return self._cached(("get", external_id), lambda: self._get(external_id))

    def _json(self, url: str, **kwargs) -> dict:
        try:
            response = self._client.get(url, **kwargs)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(f"{self.label} ist gerade nicht erreichbar") from exc
        if not isinstance(data, dict):
            raise SourceError(f"{self.label} lieferte unerwartete Daten")
        return data

    def _cached(self, key: tuple, producer):
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        value = producer()
        if len(self._cache) > 200:
            self._cache.clear()
        self._cache[key] = (time.monotonic(), value)
        return value
