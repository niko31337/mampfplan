"""Amerikanische Maße in metrische umrechnen (cup → g/ml, oz → g, fl oz → ml, °F → °C, inch → cm).

Tassen sind Volumen: Für Flüssigkeiten wird daraus ml, für bekannte trockene Zutaten über eine
Dichtetabelle Gramm (1 cup Mehl ≈ 120 g, Zucker ≈ 200 g …). Unbekannte Zutaten bekommen ml.
Esslöffel/Teelöffel bleiben Löffel, nur der Name wird deutsch (tbsp → EL, tsp → TL).
Steht die metrische Menge schon in Klammern daneben („1 lb (450 g) Hack“), wird diese genommen.
"""

import re
from dataclasses import dataclass

from app.services.ingredients import format_amount

_FRACTIONS = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 0.125}
_NUM = r"(?:\d+\s+(?:and\s+)?\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d*\s?[½¼¾⅓⅔⅛]|\d+(?:[.,]\d+)?)"
_RANGE = rf"(?P<qty>{_NUM})(?:\s*(?:-|–|to|bis)\s*(?P<upper>{_NUM}))?"

# Reihenfolge wichtig: „fl oz“ vor „oz“
_UNITS = [
    ("floz", r"fl\.?\s*oz\.?|fluid\s+ounces?"),
    ("cup", r"cups?|c\.(?=\s)"),
    ("tbsp", r"tablespoons?|tbsps?\.?|tbs\.?|tblsps?\.?"),
    ("tsp", r"teaspoons?|tsps?\.?"),
    ("oz", r"ounces?|oz\.?"),
    ("lb", r"pounds?|lbs?\.?"),
    ("pint", r"pints?|pt\.?"),
    ("quart", r"quarts?|qts?\.?"),
    ("gallon", r"gallons?|gal\.?"),
    ("stick", r"sticks?"),
]
_UNIT_RE = "|".join(f"(?P<{key}>{pattern})" for key, pattern in _UNITS)
_LINE = re.compile(rf"^\s*{_RANGE}\s*-?\s*(?:{_UNIT_RE})(?![\w])\s*(?:of\s+)?(?P<rest>.*)$", re.I)
_IN_TEXT = re.compile(rf"(?<![\w/,.]){_RANGE}\s*-?\s*(?:{_UNIT_RE})(?![\w])(?:\s*(?P<paren>\([^()]{{0,40}}\)))?", re.I)
# „(450 g)“, „(80ml)“, „(8 Tbsp; 113g)“: metrische Menge, die der Autor schon dazugeschrieben hat
_METRIC_IN_PARENS = re.compile(r"^\((?P<inner>[^()]{0,40})\)\s*(?P<rest>.*)$")
_METRIC_AMOUNT = re.compile(r"(?<![\w.,])(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>g|kg|ml|l)(?![\w])", re.I)


def _given_metric(parenthetical: str | None) -> str | None:
    found = _METRIC_AMOUNT.search(parenthetical or "")
    return f"{found['amount']} {found['unit'].lower()}" if found else None
_PARENS_US = re.compile(rf"\(\s*{_RANGE}\s*-?\s*(?:{_UNIT_RE})(?![\w])\s*\)", re.I)

ML = {"cup": 240.0, "floz": 29.57, "pint": 473.0, "quart": 946.0, "gallon": 3785.0, "oz": 29.57}
GRAMS = {"oz": 28.35, "lb": 453.6, "stick": 113.0}
SPOONS = {"tbsp": "EL", "tsp": "TL"}

# Gramm pro US-Tasse (240 ml). Spezifische Begriffe zuerst, damit „brown sugar“ nicht als „sugar“ zählt.
CUP_GRAMS: list[tuple[str, float]] = [
    ("cooked rice", 160), ("brown sugar", 220), ("powdered sugar", 120), ("icing sugar", 120), ("confectioners", 120),
    ("almond flour", 100), ("bread flour", 130), ("cake flour", 115), ("whole wheat flour", 120), ("coconut flour", 115),
    ("peanut butter", 250), ("sour cream", 230), ("cream cheese", 225), ("cottage cheese", 225), ("greek yogurt", 250),
    ("chocolate chips", 170), ("cocoa", 85), ("cornstarch", 128), ("corn starch", 128), ("cornmeal", 150),
    ("flour", 120), ("mehl", 120), ("sugar", 200), ("zucker", 200), ("butter", 227),
    ("rolled oats", 90), ("oats", 90), ("haferflocken", 90), ("rice", 185), ("reis", 185), ("quinoa", 170),
    ("lentils", 200), ("linsen", 200), ("couscous", 175), ("panko", 50), ("breadcrumbs", 110), ("bread crumbs", 110),
    ("parmesan", 100), ("cheese", 110), ("käse", 110), ("yogurt", 245), ("joghurt", 245), ("mayonnaise", 220), ("mayo", 220),
    ("honey", 340), ("honig", 340), ("raisins", 150), ("rosinen", 150), ("coconut flakes", 80), ("shredded coconut", 80),
    ("walnuts", 120), ("pecans", 110), ("almonds", 140), ("cashews", 140), ("peanuts", 145), ("nuts", 130), ("nüsse", 130),
    ("seeds", 140), ("berries", 150), ("blueberries", 150), ("strawberries", 150), ("beeren", 150),
    ("spinach", 30), ("spinat", 30), ("kale", 65), ("cilantro", 20), ("parsley", 20), ("basil", 20), ("mint", 20),
    ("koriander", 20), ("petersilie", 20), ("basilikum", 20), ("green onions", 100), ("scallions", 100), ("spring onions", 100),
    ("onion", 160), ("zwiebel", 160), ("tomatoes", 180), ("tomaten", 180), ("corn", 150), ("mais", 150), ("peas", 145),
    ("erbsen", 145), ("beans", 175), ("bohnen", 175), ("chickpeas", 165), ("kichererbsen", 165), ("mushrooms", 70),
    ("pilze", 70), ("carrots", 130), ("karotten", 130), ("potatoes", 150), ("kartoffeln", 150), ("salt", 290), ("salz", 290),
]  # fmt: skip
LIQUIDS = (
    "water", "wasser", "milk", "milch", "buttermilk", "cream", "sahne", "broth", "stock", "brühe", "juice", "saft",
    "oil", "öl", "wine", "wein", "vinegar", "essig", "sauce", "soße", "soy", "coconut milk", "kokosmilch", "beer", "bier",
    "coffee", "kaffee", "syrup", "sirup", "liquor", "rum", "vodka", "whiskey", "espresso", "puree", "passata",
)  # fmt: skip


@dataclass
class Conversion:
    before: str
    after: str


def _number(text: str) -> float:
    text = text.strip().replace(",", ".")
    mixed = re.fullmatch(r"(\d+)\s+(?:and\s+)?(\d+)\s*/\s*(\d+)", text)
    if mixed:
        return int(mixed[1]) + int(mixed[2]) / int(mixed[3])
    fraction = re.fullmatch(r"(\d+)\s*/\s*(\d+)", text)
    if fraction:
        return int(fraction[1]) / int(fraction[2]) if int(fraction[2]) else 0.0
    unicode = re.fullmatch(r"(\d*)\s?([½¼¾⅓⅔⅛])", text)
    if unicode:
        return (int(unicode[1]) if unicode[1] else 0) + _FRACTIONS[unicode[2]]
    return float(text)


def _unit_key(match: re.Match) -> str:
    return next(key for key, _ in _UNITS if match.group(key))


def _matches(name: str, word: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(word)}", name) is not None


def _is_liquid(name: str) -> bool:
    lowered = name.lower()
    if any(_matches(lowered, word) for word in ("cream cheese", "sour cream", "ice cream", "peanut butter")):
        return False
    return any(_matches(lowered, word) for word in LIQUIDS)


def _cup_grams(name: str) -> float | None:
    lowered = name.lower()
    for word, grams in CUP_GRAMS:
        if _matches(lowered, word):
            return grams
    return None


def _round(value: float) -> float:
    """Küchentauglich runden: 7 g, 45 g, 120 g, 450 g, 1250 g."""
    if value < 15:
        return round(value * 2) / 2 if value < 3 else float(round(value))
    if value < 100:
        return float(round(value / 5) * 5)
    if value < 1000:
        return float(round(value / 10) * 10)
    return float(round(value / 50) * 50)


def _metric(value: float, unit: str) -> tuple[float, str]:
    value = _round(value)
    if unit == "ml" and value >= 1000:
        return round(value / 1000, 2), "l"
    if unit == "g" and value >= 1000:
        return round(value / 1000, 2), "kg"
    return value, unit


def convert_amount(amount: float, key: str, name: str) -> tuple[float, str] | None:
    """Menge in US-Einheit → (Menge, metrische Einheit). None, wenn die Einheit nicht umgerechnet wird."""
    if key in SPOONS:
        return amount, SPOONS[key]
    if key == "stick":
        return _metric(amount * GRAMS["stick"], "g")
    if key == "lb":
        return _metric(amount * GRAMS["lb"], "g")
    if key == "oz":
        return _metric(amount * (ML["oz"] if _is_liquid(name) else GRAMS["oz"]), "ml" if _is_liquid(name) else "g")
    if key == "cup":
        grams = None if _is_liquid(name) else _cup_grams(name)
        return _metric(amount * grams, "g") if grams else _metric(amount * ML["cup"], "ml")
    if key in ML:
        return _metric(amount * ML[key], "ml")
    return None


def _format(amount: float, upper: float | None, unit: str) -> str:
    text = format_amount(amount)
    if upper is not None and upper != amount:
        text += "–" + format_amount(upper)
    return f"{text} {unit}"


def _convert_match(match: re.Match, name: str) -> str | None:
    key = _unit_key(match)
    try:
        amount = _number(match["qty"])
        upper = _number(match["upper"]) if match["upper"] else None
    except (ValueError, ZeroDivisionError):
        return None
    low = convert_amount(amount, key, name)
    if low is None:
        return None
    high = convert_amount(upper, key, name) if upper is not None else None
    if high and high[1] != low[1]:  # 0,5–2 l vs. ml: einheitlich in der kleineren Einheit bleiben
        return None
    return _format(low[0], high[0] if high else None, low[1])


def metric_line(line: str) -> str:
    """Zutatenzeile umrechnen; unverändert, wenn sie keine US-Einheit enthält."""
    match = _LINE.match(line)
    if not match:
        # „1 (14 oz) can tomatoes“: nur die Klammer umrechnen; „optional: 1 cup nuts“: Mengen im Text
        line = _PARENS_US.sub(lambda m: f"({_convert_match(m, line) or m.group(0)[1:-1]})", line)
        return metric_text(line)
    rest = match["rest"].strip()
    parens = _METRIC_IN_PARENS.match(rest)
    given = _given_metric(parens["inner"]) if parens else None
    if given:  # „1 lb (450 g) ground chicken“: die metrische Angabe steht schon da
        return f"{given} {parens['rest'].strip()}".strip()
    converted = _convert_match(match, rest)
    if converted is None:
        return line
    return f"{converted} {rest}".strip()


_FAHRENHEIT = re.compile(
    r"(?<![\w.,])(?P<f>[2-5]\d\d)\s*(?:°|º|degrees?\s*)?\s*F\b(?:\s*(?:/|\(|or)\s*(?P<c>\d{2,3})\s*(?:°|º|degrees?\s*)?\s*C\b\)?)?",
)
_INCH = re.compile(
    rf"(?<![\w/])(?P<a>{_NUM})(?:\s*(?:x|×|by)\s*(?P<b>{_NUM}))?\s*(?:-|\s)?(?:inch(?:es)?|in\.)(?![\w])", re.I
)


def _celsius(match: re.Match) -> str:
    celsius = int(match["c"]) if match["c"] else round((int(match["f"]) - 32) * 5 / 9 / 5) * 5
    return f"{celsius} °C"


def _cm(value: str) -> str:
    centimetres = _number(value) * 2.54
    return format_amount(round(centimetres * 2) / 2 if centimetres < 10 else round(centimetres))


def _inches(match: re.Match) -> str:
    try:
        if match["b"]:
            return f"{_cm(match['a'])} x {_cm(match['b'])} cm"
        return f"{_cm(match['a'])} cm"
    except (ValueError, ZeroDivisionError):
        return match.group(0)


def metric_text(text: str) -> str:
    """Fließtext (Anleitung): °F → °C, inch → cm, cup/oz/lb/fl oz → ml bzw. g, tbsp/tsp → EL/TL."""
    text = _FAHRENHEIT.sub(_celsius, text)
    text = _INCH.sub(_inches, text)

    def replace(match: re.Match) -> str:
        given = _given_metric(match["paren"])
        if given:
            return given
        following = text[match.end() : match.end() + 40]
        converted = _convert_match(match, following)
        if converted is None:
            return match.group(0)
        return f"{converted} {match['paren']}" if match["paren"] else converted

    return _IN_TEXT.sub(replace, text)


def metric_lines(lines: list[str]) -> tuple[list[str], list[Conversion]]:
    """Alle Zeilen umrechnen; zusätzlich die Liste der Änderungen (für den Hinweis in der Vorschau)."""
    result, changes = [], []
    for line in lines:
        converted = metric_line(line)
        if converted != line:
            changes.append(Conversion(line, converted))
        result.append(converted)
    return result, changes


def metric_ingredient(amount: float | None, unit: str, name: str) -> tuple[float | None, str, str]:
    """Für Rezepte aus der Online-Suche: (Menge, Einheit, Name) umrechnen."""
    key = next((k for k, pattern in _UNITS if re.fullmatch(pattern, unit.strip(), re.I)), None) if unit else None
    if amount is None or key is None:
        return amount, unit, name
    converted = convert_amount(amount, key, name)
    if converted is None:
        return amount, unit, name
    return converted[0], converted[1], name
