"""Zutaten-Zeilen ("200 g Mehl") parsen und wieder formatieren."""

import re

UNITS = {
    "g", "gr", "kg", "mg", "ml", "l", "dl", "cl", "liter", "el", "tl", "msp", "prise", "prisen",
    "bund", "bd", "dose", "dosen", "pck", "pkg", "päckchen", "packung", "packungen", "becher",
    "glas", "gläser", "scheibe", "scheiben", "zehe", "zehen", "stange", "stangen", "blatt",
    "blätter", "kopf", "tasse", "tassen", "handvoll", "stk", "stück", "st", "flasche", "flaschen",
    "cup", "cups", "tbsp", "tbs", "tsp", "oz", "lb", "lbs", "pinch", "clove", "cloves", "slice",
    "slices", "can", "cans", "bunch", "knob",
}  # fmt: skip

_UNICODE_FRACTIONS = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3}
_NUMBER = re.compile(
    r"""
    (?:(?P<mixed_i>\d+)\s+(?P<mixed_n>\d+)\s*/\s*(?P<mixed_d>\d+))   # 1 1/2
    |(?:(?P<frac_n>\d+)\s*/\s*(?P<frac_d>\d+))                       # 1/2
    |(?:(?P<int_u>\d+)?\s*(?P<uni>[½¼¾⅓⅔]))                          # ½ / 1½
    |(?P<dec>\d+(?:[.,]\d+)?)                                        # 1,5
    """,
    re.VERBOSE,
)
_RANGE_SEP = re.compile(r"\s*[-–]\s*")
_UNIT_WORD = re.compile(r"([A-Za-zÄÖÜäöüß]+)\.?")


def _match_value(m: re.Match) -> float:
    if m["mixed_i"]:
        return int(m["mixed_i"]) + int(m["mixed_n"]) / int(m["mixed_d"])
    if m["frac_n"]:
        return int(m["frac_n"]) / int(m["frac_d"])
    if m["uni"]:
        return (int(m["int_u"]) if m["int_u"] else 0) + _UNICODE_FRACTIONS[m["uni"]]
    return float(m["dec"].replace(",", "."))


def _take_number(text: str) -> tuple[float | None, str]:
    m = _NUMBER.match(text)
    if not m:
        return None, text
    value = _match_value(m)
    rest = text[m.end() :]
    sep = _RANGE_SEP.match(rest)
    if sep:  # "2-3 Tomaten" -> obere Grenze
        upper = _NUMBER.match(rest[sep.end() :])
        if upper:
            return _match_value(upper), rest[sep.end() + upper.end() :]
    return value, rest


def parse_measure(text: str) -> tuple[float | None, str, str]:
    """Zerlegt "200 g Mehl" in (200.0, "g", "Mehl"). Der Rest ist der Name bzw. Zusatztext."""
    text = text.strip().lstrip("-•* ").strip()
    amount, rest = _take_number(text)
    rest = rest.strip()
    if amount is None:
        return None, "", text
    unit = ""
    word = _UNIT_WORD.match(rest)
    if word and word[1].lower() in UNITS:
        unit = word[1]
        rest = rest[word.end() :].strip()
    return amount, unit, rest


def parse_line(line: str) -> tuple[float | None, str, str]:
    amount, unit, name = parse_measure(line)
    if not name and unit:  # "2 EL" ohne Namen -> Einheit ist der Name
        name, unit = unit, ""
    return amount, unit[:20], name[:100]


def parse_lines(text: str, limit: int = 100) -> list[tuple[float | None, str, str]]:
    result = []
    for line in text.splitlines():
        if not line.strip():
            continue
        amount, unit, name = parse_line(line)
        if name:
            result.append((amount, unit, name))
        if len(result) >= limit:
            break
    return result


def format_amount(amount: float | None) -> str:
    if amount is None:
        return ""
    if float(amount).is_integer():
        return str(int(amount))
    return f"{amount:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def format_quantity(amount: float | None, unit: str) -> str:
    return " ".join(p for p in (format_amount(amount), unit) if p)


def format_ingredient(amount: float | None, unit: str, name: str) -> str:
    return " ".join(p for p in (format_quantity(amount, unit), name) if p)
