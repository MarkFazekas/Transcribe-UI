"""Showing a backend text from the catalog entry of its code, formatted with its `details` (§3.1).

The backend returns every text as a stable code, its values in `details` and an English
`message` (for the log, the CLI's JSON and an LLM). A frontend shows the catalog entry of the
code instead; the English `message` is only the fallback for a code without an entry (or an
entry its details cannot fill, which a test rules out for every code).

An entry is a template whose `{key}` fields name details, or a function of the details that
returns the template (plural forms, sentences shown only for some values). A code whose wording
depends on one detail names that key in its table's variants; the entry `<code>.<value>` then
wins over the entry of the code. Values are shown as the active language writes them: numbers
with its separators (`{cost:.2f}` keeps two decimals), `{audio_s:duration}` as a duration,
lists joined with commas, booleans as yes or no.
"""

import logging
from collections.abc import Callable, Mapping

from pydantic import JsonValue

from transcribe_ui.i18n.catalog import tr
from transcribe_ui.i18n.constants import DECIMALS_SPEC, DURATION_SPEC, LIST_SEPARATOR, VARIANT_SEPARATOR
from transcribe_ui.i18n.formats import format_duration, format_number

logger = logging.getLogger(__name__)

# The structured values of a backend text.
type Details = Mapping[str, JsonValue]
# A catalog entry: a template, or a function of the details that returns the template.
type CodeText = str | Callable[[Details], str]
# Catalog entries by code (and `<code>.<value>` for variants).
type CodeTable = Mapping[str, CodeText]


def count_of(details: Details, key: str) -> int:
    """Return the number a detail holds, or the length of the list it holds (0 otherwise); for plural forms."""
    found = details.get(key)
    if isinstance(found, list):
        return len(found)
    if isinstance(found, int) and not isinstance(found, bool):
        return found
    return 0


class _Shown:
    """A detail value as a text shows it."""

    def __init__(self, shown: JsonValue) -> None:
        self._shown = shown

    def __format__(self, spec: str) -> str:
        shown = self._shown
        if shown is None:
            return ""
        if isinstance(shown, bool):
            return tr("yes") if shown else tr("no")
        if isinstance(shown, int | float):
            return _number(shown, spec)
        if isinstance(shown, list):
            return LIST_SEPARATOR.join(format(_Shown(entry), spec) for entry in shown)
        return format(str(shown), spec)

    def __str__(self) -> str:
        return format(self, "")


def _number(number: float, spec: str) -> str:
    if spec == DURATION_SPEC:
        return format_duration(number)
    decimals = DECIMALS_SPEC.fullmatch(spec)
    if decimals is not None:
        return format_number(number, int(decimals.group(1)))
    if isinstance(number, int):
        return format_number(number)
    return str(number)


def _entry(
    table: CodeTable,
    variants: Mapping[str, str],
    code: str,
    details: Details,
) -> CodeText | None:
    key = variants.get(code)
    if key is not None:
        variant_key = f"{code}{VARIANT_SEPARATOR}{details.get(key)}"
        variant = table.get(variant_key)
        if variant is not None:
            return variant
    return table.get(code)


def render_code(
    table: CodeTable,
    code: str,
    details: Details,
    fallback: str,
    variants: Mapping[str, str],
) -> str:
    """Return the text of `code` in the active language, or `fallback` without a usable entry."""
    entry = _entry(table, variants, code, details)
    if entry is None:
        logger.warning("No text for the code %s; its English message is shown", code)
        return fallback
    template = entry(details) if callable(entry) else entry
    fields = {key: _Shown(shown) for key, shown in details.items()}
    try:
        return template.format_map(fields)
    except (KeyError, IndexError, ValueError) as error:
        logger.warning("The text of the code %s cannot be filled from its details: %r", code, error)
        return fallback
