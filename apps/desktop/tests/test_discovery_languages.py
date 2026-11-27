"""Language parsing of sidecar subtitle names (architecture §3.2)."""

import pytest

from transcribe_ui.discovery.languages import language_of, parse_tokens

ENGLISH = "en"
HINDI = "hi"


@pytest.mark.parametrize(
    ("token", "language"),
    [
        (ENGLISH, ENGLISH),
        ("ENG", ENGLISH),
        ("English", ENGLISH),
        ("hun", "hu"),
        ("Hungarian", "hu"),
        ("ger", "de"),
        ("deu", "de"),
        ("pt-BR", "pt"),
        ("en_US", ENGLISH),
        ("zh-Hans", "zh"),
        ("iw", "he"),
        ("haw", "haw"),
        ("Haitian Creole", "ht"),
        ("farsi", "fa"),
        ("magyar", None),
        ("xx", None),
        ("forced", None),
        ("cd2", None),
    ],
)
def test_language_tokens(token: str, language: str | None) -> None:
    """ISO 639-1 and 639-2 codes, English names, aliases and region suffixes; anything else is unknown."""
    assert language_of(token) == language


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        ([], (None, False)),
        (["forced", ENGLISH], (ENGLISH, True)),
        ([ENGLISH, HINDI], (ENGLISH, False)),
        ([HINDI], (HINDI, False)),
        (["sdh"], (None, False)),
    ],
)
def test_sidecar_tokens(tokens: list[str], expected: tuple[str | None, bool]) -> None:
    """The first token naming a language decides; `forced` anywhere sets the flag."""
    assert parse_tokens(tokens) == expected
