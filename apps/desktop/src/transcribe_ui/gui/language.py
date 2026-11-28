"""The GUI's language: the catalog of the app's texts and Qt's own texts (architecture §3.1).

Resolved once at start-up, before the window is built (the `language` setting, with `system`
the OS language). Qt's own texts (standard buttons such as Cancel, context menus) come from
Qt's translation files of the same language; without them they stay English.
"""

import logging
from contextlib import ExitStack
from dataclasses import dataclass
from functools import partial

from PySide6.QtCore import QLibraryInfo, QTranslator
from PySide6.QtWidgets import QApplication

from transcribe_ui.api.classes import LanguageSetting
from transcribe_ui.gui.constants import QT_TRANSLATION_PREFIX
from transcribe_ui.i18n import system
from transcribe_ui.i18n.constants import DEFAULT_LANGUAGE, Language
from transcribe_ui.i18n.language import resolve_language, start_language

logger = logging.getLogger(__name__)


def qt_translator(language: Language) -> QTranslator | None:
    """Return the translator of the texts Qt itself shows, or None for English or when Qt has none."""
    if language == DEFAULT_LANGUAGE:
        return None
    translator = QTranslator()
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if translator.load(f"{QT_TRANSLATION_PREFIX}{language}", folder):
        return translator
    logger.warning("Qt's own %s texts are not found in %s; they stay English", language, folder)
    return None


@dataclass(frozen=True)
class StartLanguages:
    """The languages resolved at start-up: the one shown, and the one `system` means (the OS's, read once)."""

    shown: Language
    system: Language


def _found(language: str | None) -> str | None:
    return language


def use_language(
    setting: LanguageSetting,
    app: QApplication,
    stack: ExitStack,
    os_language: system.SystemLanguage | None = None,
) -> StartLanguages:
    """Activate the language of the setting for the app's and Qt's texts until `stack` closes; return both.

    The OS language is read once (on macOS a subprocess), so a later comparison never reads it again.
    """
    reader = system.os_language if os_language is None else os_language
    os_found = partial(_found, reader())
    language = start_language(setting, os_found)
    translator = qt_translator(language)
    if translator is not None:
        app.installTranslator(translator)
        stack.callback(app.removeTranslator, translator)
    return StartLanguages(shown=language, system=resolve_language("system", os_found))
