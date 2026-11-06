"""Rezept-Konverter: macht aus einem Link (Instagram, Blog, HelloFresh …) einen Rezept-Vorschlag."""

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.converter import fetch as fetching
from app.converter.fetch import FetchError
from app.converter.html import clean, meta_tags, page_title, structured_recipe, text_lines
from app.converter.text import parse_text, strip_symbols
from app.services.units import Conversion, metric_lines, metric_text

__all__ = ["CONVERTED_SOURCES", "Draft", "FetchError", "convert", "fetch_image", "image_key", "source_kind", "with_source_line"]

CONVERTED_SOURCES = ("instagram", "hellofresh", "link")
MAX_INGREDIENTS = 100
MAX_STEPS_CHARS = 18000


@dataclass
class Draft:
    source_url: str
    source: str
    title: str = ""
    servings: int | None = None
    ingredients: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    image_url: str | None = None
    needs_text: bool = False  # Seite liefert keinen Rezepttext (z. B. Instagram ohne Anmeldung)
    conversions: list[Conversion] = field(default_factory=list)  # umgerechnete US-Maße (Zutaten)
    steps_converted: bool = False  # Anleitung enthielt °F, inch, cups …

    @property
    def found(self) -> bool:
        return bool(self.ingredients or self.steps)

    @property
    def ingredient_text(self) -> str:
        return "\n".join(t for t in (tidy_ingredient(i) for i in self.ingredients[:MAX_INGREDIENTS]) if t)

    @property
    def steps_text(self) -> str:
        return with_source_line(format_steps(self.steps), self.source_url)


def source_kind(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host == "instagram.com" or host.endswith(".instagram.com"):
        return "instagram"
    if re.search(r"(^|\.)hellofresh\.[a-z.]+$", host):
        return "hellofresh"
    return "link"


_TASTE = re.compile(r"^nach (geschmack|belieben)\s+", re.I)
_BARE_UNIT = re.compile(r"^(g|kg|ml|l|EL|TL|Stück|Stk\.?|Prise|Bund|Pck\.?)\s+(?=[A-ZÄÖÜ])")


def tidy_ingredient(text: str) -> str:
    """Kleine Aufräumarbeiten: „nach Geschmack Salz“ → „Salz“, „ml Öl“ (Menge fehlt) → „Öl“."""
    text = re.sub(r"\s+", " ", text).strip(" -–•*,;")
    return _BARE_UNIT.sub("", _TASTE.sub("", text))[:150]


def source_line(url: str) -> str:
    return f"Quelle: {url}"


def with_source_line(steps: str, url: str | None) -> str:
    """Die Quelle steht immer als letzte Zeile der Zubereitung (wird beim Speichern wieder ergänzt)."""
    steps = (steps or "").rstrip()
    if not url or url in steps:
        return steps
    return f"{steps}\n\n{source_line(url)}" if steps else source_line(url)


def format_steps(steps: list[str]) -> str:
    """Schritte nummerieren (Zwischenüberschriften mit Doppelpunkt bleiben unnummeriert)."""
    lines, number = [], 0
    for step in steps:
        step = re.sub(r"\s+", " ", step).strip()
        if not step:
            continue
        if step.endswith(":") and len(step) <= 60:
            lines.append(step)
            continue
        number += 1
        lines.append(f"{number}. {step}")
    return "\n\n".join(lines)[:MAX_STEPS_CHARS]


IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")


def image_key(url: str | None) -> str | None:
    """Adresse, unter der das Bild abgelegt wird (max. 500 Zeichen; lange signierte Adressen ohne Query)."""
    if not url or not url.startswith("https://"):
        return None
    if len(url) > 500:
        url = url.split("?", 1)[0]
    return url if len(url) <= 500 else None


def fetch_image(url: str) -> bytes | None:
    """Bild des Rezepts abgesichert laden (gleiche SSRF-Regeln wie für Seiten)."""
    try:
        return fetching.fetch(url, accept=IMAGE_TYPES, max_bytes=fetching.MAX_IMAGE_BYTES).content
    except FetchError:
        return None


_IG_TITLE = re.compile(r"^.{0,200}? (?:auf|on|en|sur|su) Instagram\s*:\s*[\"“](.*)[\"”]\s*$", re.S)
_IG_DESCRIPTION = re.compile(r"^[^\"“]{0,200}?:\s*[\"“](.*)[\"”]\.?\s*$", re.S)


def instagram_caption(document: str) -> str:
    """Beschreibung eines Instagram-Posts aus den Meta-Angaben (falls Instagram sie ausliefert)."""
    meta = meta_tags(document)
    for key, pattern in (("og:title", _IG_TITLE), ("og:description", _IG_DESCRIPTION), ("description", _IG_DESCRIPTION)):
        match = pattern.match(meta.get(key, "").strip())
        if match and match[1].strip():
            return match[1].strip()
    return ""


def _clean_page_title(document: str, meta: dict[str, str]) -> str:
    title = meta.get("og:title") or page_title(document)
    site = meta.get("og:site_name", "")
    for separator in (" | ", " - ", " – ", " · "):
        if site and title.endswith(separator + site):
            title = title[: -len(separator + site)]
    return strip_symbols(clean(title))[:200]


def _from_text(draft: Draft, text: str) -> Draft:
    parsed = parse_text(text.splitlines())
    draft.title = parsed.title or draft.title
    draft.servings = parsed.servings
    draft.ingredients = parsed.ingredients
    draft.steps = parsed.steps
    return draft


def convert(url: str, pasted_text: str = "") -> Draft:
    """Vorschlag aus dem Link erstellen. Mit eingefügtem Text wird nur dieser ausgewertet.

    Amerikanische Maße (cup, oz, lb, °F …) werden dabei automatisch metrisch umgerechnet.
    """
    return _to_metric(_convert(url, pasted_text))


def _to_metric(draft: Draft) -> Draft:
    draft.ingredients, draft.conversions = metric_lines(draft.ingredients)
    steps = [metric_text(step) for step in draft.steps]
    draft.steps_converted = steps != draft.steps
    draft.steps = steps
    return draft


def _convert(url: str, pasted_text: str) -> Draft:
    url = fetching.validate_url(url)
    draft = Draft(source_url=url, source=source_kind(url))
    if pasted_text.strip():
        return _from_text(draft, pasted_text)

    try:
        page = fetching.fetch(url)
    except FetchError:
        if draft.source == "instagram":
            draft.needs_text = True
            return draft
        raise
    document = page.text()
    meta = meta_tags(document)
    draft.image_url = meta.get("og:image") if (meta.get("og:image") or "").startswith("https://") else None

    if draft.source == "instagram":
        caption = instagram_caption(document)
        if not caption:
            draft.needs_text = True
            return draft
        return _from_text(draft, caption)

    structured = structured_recipe(document, meta.get("og:title") or page_title(document))
    if structured and (structured.ingredients or structured.steps):
        draft.title = structured.title or _clean_page_title(document, meta)
        draft.servings = structured.servings
        draft.ingredients = structured.ingredients
        draft.steps = structured.steps
        draft.image_url = structured.image_url or draft.image_url
        return draft

    parsed = parse_text(text_lines(document))
    draft.title = parsed.title or _clean_page_title(document, meta)
    draft.servings = parsed.servings
    draft.ingredients = parsed.ingredients
    draft.steps = parsed.steps
    return draft


