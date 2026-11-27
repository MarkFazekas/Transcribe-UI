"""Language and flags of a sidecar subtitle from the tokens of its name (architecture §3.2)."""

from collections.abc import Mapping, Sequence
from functools import cache

from transcribe_contracts.constants import WHISPER_LANGUAGES
from transcribe_ui.discovery.constants import (
    FORCED_MARKER,
    ISO_THREE_LETTER_CODES,
    LANGUAGE_ALIASES,
    LANGUAGE_REGION_SEPARATORS,
)


@cache
def _lookup() -> Mapping[str, str]:
    """Return every known spelling (case-folded) -> Whisper language code."""
    names = {name.casefold(): code for code, name in WHISPER_LANGUAGES.items()}
    codes = {code: code for code in WHISPER_LANGUAGES}
    return {**names, **ISO_THREE_LETTER_CODES, **LANGUAGE_ALIASES, **codes}


def _without_region(text: str) -> str:
    for separator in LANGUAGE_REGION_SEPARATORS:
        text = text.split(separator, maxsplit=1)[0]
    return text


def language_of(token: str) -> str | None:
    """Return the Whisper language code a name token stands for, or None.

    Accepts ISO 639-1 and 639-2/B/T codes, the English names, a few other spellings, and a
    code with a region or script suffix (`pt-BR`, `en_US`, `zh-Hans`).
    """
    text = token.strip().casefold()
    lookup = _lookup()
    found = lookup.get(text)
    if found is None:
        found = lookup.get(_without_region(text))
    return found


def parse_tokens(tokens: Sequence[str]) -> tuple[str | None, bool]:
    """Return the language (the first token naming one; None = unknown) and the forced flag."""
    forced = any(token.casefold() == FORCED_MARKER for token in tokens)
    languages = (language_of(token) for token in tokens)
    language = next((code for code in languages if code is not None), None)
    return language, forced
