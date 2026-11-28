"""The GUI's language: the catalog of the app's texts and Qt's own texts (architecture §3.1).

Resolved once at start-up, before the window is built (the `language` setting, with `system`
the OS language). Qt's own texts (standard buttons such as Cancel, context menus) come from
Qt's translation files of the same language; without them they stay English.
"""

import logging
from contextlib import ExitStack

from PySide6.QtCore import QLibraryInfo, QTranslator
from PySide6.QtWidgets import QApplication

from transcribe_ui.api.classes import LanguageSetting
from transcribe_ui.gui.constants import QT_TRANSLATION_PREFIX
from transcribe_ui.i18n.constants import DEFAULT_LANGUAGE, Language
from transcribe_ui.i18n.language import start_language
from transcribe_ui.i18n.system import SystemLanguage

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


def use_language(
    setting: LanguageSetting,
    app: QApplication,
    stack: ExitStack,
    os_language: SystemLanguage | None = None,
) -> Language:
    """Activate the language of the setting for the app's and Qt's texts until `stack` closes; return it."""
    language = start_language(setting, os_language)
    translator = qt_translator(language)
    if translator is not None:
        app.installTranslator(translator)
        stack.callback(app.removeTranslator, translator)
    return language
