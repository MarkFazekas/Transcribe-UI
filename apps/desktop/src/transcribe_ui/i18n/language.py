"""Which language a frontend shows its texts in: the setting, else the OS language (Q32).

`system` follows the OS user interface language: Hungarian when it is `hu`, otherwise English.
Each frontend resolves the setting once at start-up (`Backend.language_setting`, `system` when
the settings file cannot be read) and activates the catalog; a change applies after a restart.
"""

from transcribe_ui.api.classes import LanguageSetting
from transcribe_ui.i18n import system
from transcribe_ui.i18n.catalog import activate
from transcribe_ui.i18n.constants import DEFAULT_LANGUAGE, HUNGARIAN, Language


def resolve_language(setting: LanguageSetting, os_language: system.SystemLanguage) -> Language:
    """Return the language of the texts: the setting, or with `system` the OS language (`hu` or English)."""
    if setting != "system":
        return setting
    return HUNGARIAN if os_language() == HUNGARIAN else DEFAULT_LANGUAGE


def start_language(setting: LanguageSetting, os_language: system.SystemLanguage | None = None) -> Language:
    """Resolve the setting (the real OS language unless `os_language` is given), activate it and return it."""
    reader = system.os_language if os_language is None else os_language
    language = resolve_language(setting, reader)
    activate(language)
    return language
