"""Rezept aus Fließtext erkennen: Instagram-Beschreibungen und Blogartikel ohne Rezept-Markup.

Gesucht werden Abschnitte wie „Zutaten“ / „Ingredients“ und „Zubereitung“ / „Instructions“.
Die Erkennung ist bewusst einfach und tolerant – das Ergebnis ist ein Vorschlag, den man vor dem
Speichern noch anpassen kann.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from app.converter.html import Line

_INGREDIENT_HEADING = re.compile(
    r"(zutaten(liste)?|du brauchst|ihr braucht|was du brauchst|einkaufsliste|ingredients?|rezept|recipe)(?![\w-])",
    re.I,
)
_STEPS_HEADING = re.compile(
    r"(zubereitung|anleitung|und so geht'?s|so geht'?s|so wird'?s gemacht|schritte|instructions?|directions?"
    r"|method|preparation|how to make it)(?![\w-])",
    re.I,
)
_NUMBER_WORDS = {"ein": 1, "eine": 1, "einen": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6, "acht": 8, "zehn": 10, "two": 2, "three": 3, "four": 4, "six": 6}  # fmt: skip
_SERVINGS = re.compile(
    r"(\d+|" + "|".join(_NUMBER_WORDS) + r")(?:\s*[-–]\s*\d+)?\s*"
    r"(portionen|portion|personen|person|pers\.?|servings?|portions?|people|schüsseln|bowls?|teller)\b",
    re.I,
)
_NUMBERED = re.compile(r"^\s*\d{1,2}\s*[.)]\s+")
_KEYCAP = re.compile("([0-9])️?⃣")  # 1️⃣ → 1.
_LEADING_SYMBOLS = re.compile(r"^[^\w(„“\"'’]+")
_HASHTAG = re.compile(r"#[\w-]+")
_PROMO = re.compile(
    r"link in (der |meiner )?bio|folge? (mir|uns)|follow (me|us|for)|speicher(e|t)? (dir )?(das|den|dieses)|save (this|it|for)"
    r"|teil(e|t) (das|den|dieses)|share (this|with)|comment\b|kommentier|markier(e|t)|tag (a|someone)|abonnier|werbung|anzeige",
    re.I,
)
_GROUP_START = re.compile(r"^(für|for|zum|zur|zusätzlich|außerdem|optional|topping|garnish|deko|sauce|soße|dressing|dip|teig|füllung|marinade)\b", re.I)


_KEEP_SYMBOLS = set("°℃℉©®")


@dataclass
class TextRecipe:
    title: str = ""
    servings: int | None = None
    ingredients: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)


def strip_symbols(text: str) -> str:
    """Emojis, Flaggen, Pfeile usw. entfernen (Buchstaben, Ziffern und Satzzeichen bleiben)."""
    kept = []
    for char in text:
        category = unicodedata.category(char)
        if char in _KEEP_SYMBOLS:
            pass
        elif category in ("So", "Sk", "Cs", "Co", "Cn") or char in "‍️︎⃣":
            continue
        kept.append(char)
    return re.sub(r"\s+", " ", "".join(kept)).strip()


def _normalise(raw: str) -> str:
    return _KEYCAP.sub(r"\1.", raw).strip()


def _has_marker(raw: str) -> bool:
    """Zeile beginnt mit Aufzählungszeichen (-, •, ▫️, ✅ …) oder Nummer („1.“)."""
    text = _normalise(raw)
    return bool(_NUMBERED.match(text) or (text and _LEADING_SYMBOLS.match(text)))


def _marker(raw: str) -> str:
    text = _normalise(raw)
    if _NUMBERED.match(text):
        return "#"
    match = _LEADING_SYMBOLS.match(text)
    return strip_symbols(match[0]) or match[0].strip()[:1] if match else ""


def _content(raw: str) -> str:
    """Zeile ohne Aufzählungszeichen und Emojis."""
    text = _normalise(raw)
    text = _NUMBERED.sub("", text)
    text = _LEADING_SYMBOLS.sub("", text)
    return strip_symbols(text).rstrip("*").strip()


def _is_hashtags(text: str) -> bool:
    tags = _HASHTAG.findall(text)
    return bool(tags) and len(" ".join(tags)) >= 0.5 * len(text.strip())


def _is_stop(raw: str) -> bool:
    text = strip_symbols(raw)
    return _is_hashtags(text) or bool(_PROMO.search(text)) or "@" in text


def find_servings(text: str) -> int | None:
    match = _SERVINGS.search(text)
    if not match:
        return None
    word = match[1].lower()
    number = int(word) if word.isdigit() else _NUMBER_WORDS.get(word, 0)
    return number if 0 < number <= 100 else None


def _heading(raw: str):
    """(Art, Rest-der-Überschrift, Inhalt-nach-Doppelpunkt) oder None."""
    text = _content(raw)
    for kind, pattern in (("ingredients", _INGREDIENT_HEADING), ("steps", _STEPS_HEADING)):
        match = pattern.match(text)
        if not match:
            continue
        head, colon, inline = text[match.end() :].partition(":")
        head = head.strip()
        if len(head) > 45:
            continue
        if head and not colon and not re.match(r"^(\(|für|for|fuer|\d)", head, re.I):
            continue  # Satz wie „Rezept findet ihr im Blog“
        return kind, head, inline.strip()
    return None


def _is_group_label(raw: str) -> bool:
    """Zwischenüberschrift in der Zutatenliste („Für die Soße“, „Sauce:“)."""
    text = _content(raw)
    if not text or len(text) > 40 or re.match(r"^[\d½¼¾⅓⅔]", text):
        return False
    return text.endswith(":") or bool(_GROUP_START.match(text)) and not re.search(r"\d", text[:3])


def _split_inline(text: str) -> list[str]:
    """„-1 lb Hack -2 Eier“ oder „Mehl, Zucker, Eier“ in einzelne Zutaten zerlegen."""
    text = text.strip()
    if not text:
        return []
    if text.startswith(("-", "–", "•")):
        parts = re.split(r"(?:^|\s)[-–•]\s*(?=\S)", text)
    elif ", " in text and not re.search(r"\d+, \d", text):
        parts = text.split(", ")
    else:
        parts = [text]
    return [p for p in (_content(p) for p in parts) if p]


def _ingredient(raw: str) -> list[str]:
    text = _content(raw)
    label, colon, inline = text.partition(":")
    if colon and inline.strip() and len(label) <= 40 and _is_group_label(label + ":"):
        return _split_inline(inline)
    return [text] if text else []


def _title_from(lines: list[str]) -> str:
    for raw in lines:
        text = strip_symbols(_HASHTAG.sub("", raw)).strip(" -–:")
        if not text:
            continue
        if _heading(raw):
            return ""
        for separator in (". ", "! ", "? ", " – ", " - ", " | "):
            if len(text) > 60 and separator in text:
                text = text.split(separator, 1)[0]
        text = text[:120].strip()
        if text.isupper():
            text = text.title()
        return text
    return ""


def parse_text(lines: list[Line] | list[str], title_hint: str = "") -> TextRecipe:
    rows = [line if isinstance(line, Line) else Line(line) for line in lines]
    result = TextRecipe()

    start = next((i for i, row in enumerate(rows) if row.text and (h := _heading(row.text)) and h[0] == "ingredients"), None)
    steps_at = next((i for i, row in enumerate(rows) if row.text and (h := _heading(row.text)) and h[0] == "steps"), None)
    if start is None and steps_at is None:
        return result
    if start is not None and steps_at is not None and steps_at < start:
        start = None  # Anleitung vor den Zutaten: dann nur die Anleitung übernehmen

    result.title = title_hint or _title_from([r.text for r in rows[: start if start is not None else steps_at]])
    if not title_hint:
        # Blog: die letzte Überschrift vor den Zutaten ist meist der Rezeptname
        before = rows[: start if start is not None else steps_at]
        headings = [r.text for r in before if r.heading in (1, 2, 3) and r.text]
        if headings:
            result.title = strip_symbols(headings[-1])

    if start is not None:
        _, head, inline = _heading(rows[start].text)
        result.servings = find_servings(head)
        result.ingredients.extend(_split_inline(inline))
        end = steps_at if steps_at is not None else len(rows)
        if steps_at is not None:
            _collect_ingredients(rows[start + 1 : end], result)
        else:
            _collect_without_steps_heading(rows[start + 1 :], result)

    if steps_at is not None:
        _, _, inline = _heading(rows[steps_at].text)
        if inline:
            result.steps.append(_content(inline))
        result.steps.extend(_collect_steps(rows[steps_at + 1 :]))

    if result.servings is None:
        result.servings = find_servings(" ".join(r.text for r in rows))
    return result


def _collect_ingredients(rows: list[Line], result: TextRecipe) -> None:
    for row in rows:
        if not row.text or row.heading:
            continue
        if _is_stop(row.text):
            break
        if _is_group_label(row.text):
            servings = find_servings(row.text)
            if servings and result.servings is None:
                result.servings = servings
            # „Für die Soße: -Sojasoße -Zucker“: Zutaten stehen in derselben Zeile
            result.ingredients.extend(_ingredient(row.text) if ":" in _content(row.text)[:-1] else [])
            continue
        result.ingredients.extend(_ingredient(row.text))


def _collect_without_steps_heading(rows: list[Line], result: TextRecipe) -> None:
    """Keine Überschrift für die Anleitung.

    - Zutaten mit Aufzählungszeichen: markierte Zeilen sind Zutaten, die übrigen Anleitung
      (Stil „-400 g Tofu / Mit der Reibe zerkleinern / -2 EL Sojasoße …“).
    - Sonst: Zutaten bis zur ersten Leerzeile; danach folgt nur eine nummerierte Anleitung.
    """
    block = []
    for index, row in enumerate(rows):
        if row.heading or (row.text and _is_stop(row.text)):
            break
        if _is_spacer(row) and _second_recipe_ahead(rows, index):
            break
        block.append(row)
    filled = [r for r in block if r.text and not _is_group_label(r.text)]
    marked = [r for r in filled if _has_marker(r.text) and not _NUMBERED.match(_normalise(r.text))]
    if filled and len(marked) >= 0.4 * len(filled):
        for row in block:
            if not row.text or _is_group_label(row.text):
                continue
            if row in marked:
                result.ingredients.extend(_ingredient(row.text))
            elif _content(row.text):
                result.steps.append(_content(row.text))
        return
    rest = 0
    for rest, row in enumerate(block):
        if not row.text:
            if result.ingredients:
                break
            continue
        if not _is_group_label(row.text):
            result.ingredients.extend(_ingredient(row.text))
    following = [r for r in block[rest + 1 :] if r.text]
    if following and _NUMBERED.match(_normalise(following[0].text)):
        result.steps.extend(_collect_steps(block[rest + 1 :]))


def _is_spacer(row: Line) -> bool:
    """Leerzeile oder Platzhalter wie „-“ / „.“ (Instagram verschluckt echte Leerzeilen)."""
    return not row.text or not _content(row.text)


def _second_recipe_ahead(rows: list[Line], index: int) -> bool:
    """Beginnt nach diesem Absatz ein weiteres Rezept (z. B. dasselbe Rezept auf Deutsch)?

    Geprüft wird bis zur nächsten Zeile mit Aufzählungszeichen: steht davor eine Zutaten-Überschrift,
    endet das erste Rezept hier.
    """
    for row in rows[index + 1 :]:
        if _is_spacer(row):
            continue
        heading = _heading(row.text)
        if heading and heading[0] == "ingredients":
            return True
        if row.heading or _has_marker(row.text):
            return False
    return False


def _collect_steps(rows: list[Line]) -> list[str]:
    """Schritte bis zu Hashtags, Werbung, der nächsten Überschrift oder dem Ende der Aufzählung."""
    steps: list[str] = []
    style = None  # Aufzählungszeichen der ersten Zeile; "" = Fließtext
    after_blank = False
    for index, row in enumerate(rows):
        if row.heading:
            break
        if _is_spacer(row):
            if _second_recipe_ahead(rows, index):
                break
            after_blank = True
            continue
        heading = _heading(row.text)
        if heading and heading[0] == "ingredients":
            break
        if _is_stop(row.text):
            break
        marker = _marker(row.text) if _has_marker(row.text) else ""
        if style is None:
            style = marker
        elif after_blank and style and marker != style:
            break  # Aufzählung zu Ende, danach kommt nur noch Plauderei
        after_blank = False
        text = _content(row.text)
        if text:
            steps.append(text)
    return steps
