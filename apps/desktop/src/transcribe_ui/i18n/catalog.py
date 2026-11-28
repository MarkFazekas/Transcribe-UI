"""The active catalog and the marking functions `tr` and `ntr` (architecture §3.1 "Language of the texts").

The catalogs are GNU gettext files read with the standard library (`locale/<language>/
LC_MESSAGES/transcribe_ui.mo`, package data); the msgids are the English texts. A frontend
activates its language once at start-up, before it builds its first text, so texts must never
be translated at import time: constants hold no texts, and code calls `tr()` where it shows one.
Texts are marked with `tr` and `ntr`, never `_` (the linters keep `_` for unused names).
"""

import gettext
import io
import logging
from dataclasses import dataclass, field
from importlib.resources import files

from transcribe_ui.i18n.constants import (
    COMPILED_SUFFIX,
    DEFAULT_LANGUAGE,
    DOMAIN,
    LOCALE_FOLDER,
    LOCALE_PACKAGE,
    MESSAGES_FOLDER,
    Language,
)

logger = logging.getLogger(__name__)


@dataclass
class _Catalog:
    """The language whose texts are shown, and its translations."""

    language: Language = DEFAULT_LANGUAGE
    translations: gettext.NullTranslations = field(default_factory=gettext.NullTranslations)


# The active catalog of this process (English until a frontend activates its language).
_active = _Catalog()


def load_translations(language: Language) -> gettext.NullTranslations:
    """Return the compiled catalog of a language; the msgids (English) when it cannot be read."""
    name = f"{DOMAIN}{COMPILED_SUFFIX}"
    resource = files(LOCALE_PACKAGE).joinpath(LOCALE_FOLDER, language, MESSAGES_FOLDER, name)
    try:
        return gettext.GNUTranslations(io.BytesIO(resource.read_bytes()))
    except (OSError, UnicodeDecodeError):
        # `GNUTranslations` raises OSError for a damaged catalog, too.
        logger.warning("The %s catalog cannot be read; the English texts are shown", language, exc_info=True)
        return gettext.NullTranslations()


def activate(language: Language) -> None:
    """Show the texts of `language` from now on (each frontend calls it once at start-up)."""
    _active.translations = load_translations(language)
    _active.language = language
    logger.info("Texts are shown in %s", language)


def active_language() -> Language:
    """Return the language of the texts shown."""
    return _active.language


def tr(message: str) -> str:
    """Return the text `message` (its English msgid) in the active language."""
    return _active.translations.gettext(message)


def ntr(singular: str, plural: str, count: int) -> str:
    """Return the text for `count` things in the active language (English: `singular` for 1, else `plural`)."""
    return _active.translations.ngettext(singular, plural, count)
