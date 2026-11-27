"""The OS user interface language: the port the frontends resolve the `system` setting with.

Windows: `GetUserDefaultUILanguage`. macOS: the first entry of `defaults read -g
AppleLanguages`. Linux and other POSIX systems: the first set variable of `LANGUAGE`, `LC_ALL`,
`LC_MESSAGES`, `LANG` (the first entry of `LANGUAGE`'s `:`-separated list). Each value is cut at
`_`, `.`, `@` or `-` and lower-cased (`hu_HU.UTF-8@euro` is `hu`); `C` and `POSIX` mean English.
Every read that fails gives None (the language is not known), never an error.
"""

import ctypes
import locale
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Mapping

from transcribe_ui.i18n.constants import (
    APPLE_LANGUAGES_COMMAND,
    APPLE_LANGUAGES_TIMEOUT_S,
    APPLE_LIST_PUNCTUATION,
    DEFAULT_LANGUAGE,
    ENGLISH_LOCALES,
    LANGUAGE_LIST_SEPARATOR,
    LANGUAGE_LIST_VARIABLE,
    LOCALE_NAME_END,
    POSIX_LANGUAGE_VARIABLES,
)

logger = logging.getLogger(__name__)

# Returns the code of the OS user interface language (lower case, like `hu`), or None if unknown.
type SystemLanguage = Callable[[], str | None]
# Returns the LANGID of the Windows user interface language.
type WindowsLanguageId = Callable[[], int]
# Returns what `defaults read -g AppleLanguages` prints.
type AppleLanguages = Callable[[], str]


def language_part(name: str) -> str | None:
    """Return the language of a locale name, lower case (`hu_HU.UTF-8@euro`: `hu`), or None if empty."""
    head = LOCALE_NAME_END.split(name.strip(), maxsplit=1)[0]
    language = head.lower()
    if not language:
        return None
    return DEFAULT_LANGUAGE if language in ENGLISH_LOCALES else language


def posix_language(environ: Mapping[str, str]) -> str | None:
    """Return the language of the first set variable of `LANGUAGE`, `LC_ALL`, `LC_MESSAGES`, `LANG`."""
    for name in POSIX_LANGUAGE_VARIABLES:
        setting = environ.get(name, "")
        if name == LANGUAGE_LIST_VARIABLE:
            setting = setting.split(LANGUAGE_LIST_SEPARATOR, maxsplit=1)[0]
        if setting:
            return language_part(setting)
    return None


def windows_language(language_id: WindowsLanguageId) -> str | None:
    """Return the language of the Windows user interface language (its LANGID's locale name)."""
    try:
        identifier = language_id()
    except OSError:
        logger.warning("The Windows user interface language cannot be read", exc_info=True)
        return None
    name = locale.windows_locale.get(identifier)
    return None if name is None else language_part(name)


def apple_language(read: AppleLanguages) -> str | None:
    """Return the language of the first entry `defaults read -g AppleLanguages` prints."""
    try:
        printed = read()
    except (OSError, subprocess.SubprocessError):
        logger.warning("The macOS languages cannot be read", exc_info=True)
        return None
    for line in printed.splitlines():
        entry = line.strip(APPLE_LIST_PUNCTUATION)
        if entry:
            return language_part(entry)
    return None


def windows_ui_language_id() -> int:
    """Return the LANGID of the Windows user interface language (`GetUserDefaultUILanguage`).

    Raises:
        OSError: Not on Windows.
    """
    if sys.platform != "win32":
        msg = "GetUserDefaultUILanguage exists only on Windows"
        raise OSError(msg)
    return int(ctypes.windll.kernel32.GetUserDefaultUILanguage())


def read_apple_languages() -> str:
    """Return what `defaults read -g AppleLanguages` prints.

    Raises:
        OSError: The command cannot be started.
        subprocess.SubprocessError: It failed or did not finish in time.
    """
    completed = subprocess.run(  # noqa: S603 - a fixed command line, no input of the user
        APPLE_LANGUAGES_COMMAND,
        capture_output=True,
        text=True,
        timeout=APPLE_LANGUAGES_TIMEOUT_S,
        check=True,
    )
    return completed.stdout


def platform_language(
    platform: str,
    environ: Mapping[str, str],
    language_id: WindowsLanguageId,
    apple_languages: AppleLanguages,
) -> str | None:
    """Return the OS user interface language on `platform` (a `sys.platform` value)."""
    if platform == "win32":
        return windows_language(language_id)
    if platform == "darwin":
        return apple_language(apple_languages)
    return posix_language(environ)


def os_language() -> str | None:
    """Return the user interface language of this computer's OS, or None if it is not known."""
    return platform_language(sys.platform, os.environ, windows_ui_language_id, read_apple_languages)
