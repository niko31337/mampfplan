"""HTML auswerten: Meta-Angaben, schema.org-Rezepte (JSON-LD) und lesbarer Text als Zeilen."""

import html as html_lib
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser


@dataclass
class Line:
    text: str
    heading: int = 0  # 1–6 bei <h1>…<h6>, sonst 0


@dataclass
class StructuredRecipe:
    title: str = ""
    servings: int | None = None
    ingredients: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    image_url: str | None = None


# --- Text ----------------------------------------------------------------------------------

_SKIP = {"script", "style", "noscript", "template", "svg", "nav", "aside", "footer", "form", "iframe", "button", "select", "textarea", "head"}
_PARAGRAPH = {"p", "ul", "ol", "table", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "figure"}
_LINE = {"br", "li", "div", "tr", "dt", "dd", "figcaption", "header", "main", "pre"}
_VOID = {"br", "img", "hr", "input", "meta", "link", "source", "wbr", "area", "col", "embed", "param", "track"}
_SPACES = re.compile(r"[ \t\r\f\v   ]+")


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines: list[Line] = []
        self._buffer: list[str] = []
        self._skip_depth = 0
        self._heading = 0

    def _flush(self, paragraph: bool = False) -> None:
        text = _SPACES.sub(" ", "".join(self._buffer)).strip()
        self._buffer = []
        if text:
            self.lines.append(Line(text, self._heading))
        if paragraph and self.lines and self.lines[-1].text:
            self.lines.append(Line(""))

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in _PARAGRAPH or tag in _LINE:
            self._flush(paragraph=tag in _PARAGRAPH)
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading = int(tag[1])

    def handle_startendtag(self, tag, attrs):
        if not self._skip_depth and tag == "br":
            self._flush()

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth or tag in _VOID:
            return
        if tag in _PARAGRAPH or tag in _LINE:
            self._flush(paragraph=tag in _PARAGRAPH)
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading = 0

    def handle_data(self, data):
        if not self._skip_depth:
            self._buffer.append(data.replace("\n", " "))

    def close(self):
        super().close()
        self._flush()


def text_lines(document: str) -> list[Line]:
    """Sichtbarer Text als Zeilen; leere Zeilen trennen Absätze."""
    parser = _TextExtractor()
    body = document
    match = re.search(r"<body[^>]*>", document, re.I)
    if match:
        body = document[match.end() :]
    parser.feed(body)
    parser.close()
    result: list[Line] = []
    for line in parser.lines:
        if not line.text and (not result or not result[-1].text):
            continue
        result.append(line)
    return result


# --- Meta-Angaben ----------------------------------------------------------------------------

_META = re.compile(r"<meta\s[^>]*>", re.I)
_ATTR = re.compile(r"""([a-zA-Z:_-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def meta_tags(document: str) -> dict[str, str]:
    """og:…/twitter:…/description aus dem <head> (erstes Vorkommen gewinnt)."""
    found: dict[str, str] = {}
    head = document[: document.lower().find("<body")] if "<body" in document.lower() else document[:200_000]
    for tag in _META.findall(head):
        attrs = {m[1].lower(): (m[2] if m[2] is not None else m[3]) for m in _ATTR.finditer(tag)}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key and "content" in attrs and key not in found:
            found[key] = html_lib.unescape(attrs["content"])
    return found


def page_title(document: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", document, re.I | re.S)
    return clean(html_lib.unescape(match[1])) if match else ""


# --- schema.org/Recipe -----------------------------------------------------------------------

_LD_JSON = re.compile(r"""<script[^>]+type=["']application/ld\+json["'][^>]*>(.*?)</script>""", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")


def clean(text: str) -> str:
    """HTML-Reste entfernen, Entities auflösen, Leerraum zusammenfassen."""
    text = html_lib.unescape(_TAGS.sub(" ", str(text)))
    return _SPACES.sub(" ", text.replace("\n", " ")).strip()


def _is_recipe(node) -> bool:
    kind = node.get("@type") if isinstance(node, dict) else None
    return kind == "Recipe" or (isinstance(kind, list) and "Recipe" in kind)


def _walk(node):
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in node:
                yield from _walk(node[key])


def _first_int(value) -> int | None:
    if isinstance(value, list):
        for item in value:
            found = _first_int(item)
            if found:
                return found
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = int(value)
    else:
        match = re.search(r"\d+", str(value or ""))
        number = int(match[0]) if match else 0
    return number if 0 < number <= 100 else None


def _image(value) -> str | None:
    if isinstance(value, list):
        for item in value:
            found = _image(item)
            if found:
                return found
        return None
    if isinstance(value, dict):
        return _image(value.get("url") or value.get("contentUrl"))
    if isinstance(value, str) and value.startswith("https://"):
        return value.strip()
    return None


def _instructions(value) -> list[str]:
    """recipeInstructions: Text, Liste von Texten, HowToStep oder HowToSection."""
    if isinstance(value, str):
        text = html_lib.unescape(value)
        parts = re.split(r"\n\s*\n|<br\s*/?>|</p>|</li>", text) if "\n\n" in text or "<" in text else text.split("\n")
        return [c for c in (clean(p) for p in parts) if c]
    steps: list[str] = []
    if isinstance(value, list):
        for item in value:
            steps.extend(_instructions(item))
    elif isinstance(value, dict):
        if value.get("@type") == "HowToSection" or "itemListElement" in value:
            name = clean(value.get("name", ""))
            inner = _instructions(value.get("itemListElement", []))
            if name and inner:
                steps.append(f"{name}:")
            steps.extend(inner)
        else:
            text = clean(value.get("text") or value.get("name") or "")
            if text:
                steps.append(text)
    return steps


def _to_structured(node: dict) -> StructuredRecipe:
    ingredients = node.get("recipeIngredient") or node.get("ingredients") or []
    if isinstance(ingredients, str):
        ingredients = [ingredients]
    return StructuredRecipe(
        title=clean(node.get("name", "")),
        servings=_first_int(node.get("recipeYield")),
        ingredients=[c for c in (clean(i) for i in ingredients if isinstance(i, str)) if c],
        steps=_instructions(node.get("recipeInstructions", [])),
        image_url=_image(node.get("image")),
    )


def structured_recipe(document: str, page_title: str = "") -> StructuredRecipe | None:
    """Rezept aus JSON-LD. Stehen mehrere auf der Seite (z. B. Hauptrezept + Gewürzmischung), gewinnt das,
    dessen Name im Seitentitel vorkommt, sonst das mit den meisten Zutaten."""
    recipes: list[StructuredRecipe] = []
    for raw in _LD_JSON.findall(document):
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        recipes.extend(_to_structured(node) for node in _walk(data) if _is_recipe(node))
    if len(recipes) <= 1:
        return recipes[0] if recipes else None
    wanted = page_title.casefold()
    titled = [r for r in recipes if r.title and wanted and (r.title.casefold() in wanted or wanted in r.title.casefold())]
    return max(titled or recipes, key=lambda r: (len(r.ingredients), len(r.steps)))
