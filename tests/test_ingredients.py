import pytest

from app.services.ingredients import format_ingredient, parse_line, parse_lines


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("200 g Mehl", (200.0, "g", "Mehl")),
        ("200g Mehl", (200.0, "g", "Mehl")),
        ("2 Eier", (2.0, "", "Eier")),
        ("1,5 l Milch", (1.5, "l", "Milch")),
        ("1/2 TL Salz", (0.5, "TL", "Salz")),
        ("1 1/2 EL Öl", (1.5, "EL", "Öl")),
        ("½ Bund Petersilie", (0.5, "Bund", "Petersilie")),
        ("2-3 Tomaten", (3.0, "", "Tomaten")),
        ("Salz", (None, "", "Salz")),
        ("- 3 Dosen Tomaten", (3.0, "Dosen", "Tomaten")),
        ("2 EL", (2.0, "", "EL")),
    ],
)
def test_parse_line(line, expected):
    assert parse_line(line) == expected


def test_parse_lines_skips_blank_lines():
    assert [name for _, _, name in parse_lines("Mehl\n\n  \n2 Eier\n")] == ["Mehl", "Eier"]


def test_format_roundtrip():
    assert format_ingredient(1.5, "l", "Milch") == "1,5 l Milch"
    assert format_ingredient(2.0, "", "Eier") == "2 Eier"
    assert format_ingredient(None, "", "Salz") == "Salz"
