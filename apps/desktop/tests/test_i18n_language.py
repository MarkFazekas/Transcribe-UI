"""Which language the frontends show: the setting, the OS language port, the settings file (plan m6 M6.14)."""

import subprocess
from pathlib import Path
from typing import Final

import pytest

from support.fakes import seeded_ports
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import LanguageSetting
from transcribe_ui.i18n import catalog
from transcribe_ui.i18n.language import resolve_language, start_language
from transcribe_ui.i18n.system import apple_language, platform_language, posix_language, windows_language
from transcribe_ui.state.settings import SettingsStore, default_settings

ENGLISH: Final = "en"
HUNGARIAN: Final = "hu"
GERMAN = "de"
SYSTEM: Final = "system"
LANG = "LANG"
LANGUAGE = "LANGUAGE"
LC_ALL = "LC_ALL"
US_ENGLISH = "en_US.UTF-8"
HUNGARIAN_LOCALE = "hu_HU.UTF-8"
# Windows language identifiers (LANGID) of the user interface languages used here.
HUNGARIAN_ID = 0x40E
ENGLISH_ID = 0x409
GERMAN_ID = 0x407
UNKNOWN_ID = 0x7FFF
# What `defaults read -g AppleLanguages` prints with Hungarian first, and with English only.
APPLE_HUNGARIAN = '(\n    "hu-HU",\n    "en-US"\n)\n'
APPLE_ENGLISH = "(\n    en\n)\n"
# The macOS command and its time limit, as a hanging `defaults` reports them.
DEFAULTS_COMMAND = "defaults"
DEFAULTS_TIMEOUT_S = 5


@pytest.mark.parametrize(
    ("setting", "os_language", "shown"),
    [
        (ENGLISH, HUNGARIAN, ENGLISH),
        (HUNGARIAN, ENGLISH, HUNGARIAN),
        (HUNGARIAN, None, HUNGARIAN),
        (SYSTEM, HUNGARIAN, HUNGARIAN),
        (SYSTEM, ENGLISH, ENGLISH),
        (SYSTEM, GERMAN, ENGLISH),
        (SYSTEM, None, ENGLISH),
    ],
)
def test_the_setting_overrides_the_os(setting: LanguageSetting, os_language: str | None, shown: str) -> None:
    """`en` and `hu` win over the OS; `system` follows it: Hungarian for `hu`, English for any other or none."""
    assert resolve_language(setting, lambda: os_language) == shown


def test_start_language_activates_the_catalog() -> None:
    """The resolved language's catalog is active afterwards: the texts are Hungarian."""
    assert start_language(SYSTEM, lambda: HUNGARIAN) == HUNGARIAN
    assert (catalog.active_language(), catalog.tr("Retry")) == (HUNGARIAN, "Újra")
    assert start_language(SYSTEM, lambda: None) == ENGLISH
    assert catalog.tr("Retry") == "Retry"


@pytest.mark.parametrize("compiled", [b"damaged", None])
def test_a_broken_catalog_falls_back_to_english(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    compiled: bytes | None,
) -> None:
    """A damaged or missing `.mo` shows the English texts instead of failing the start."""
    folder = tmp_path / "locale" / HUNGARIAN / "LC_MESSAGES"
    folder.mkdir(parents=True)
    if compiled is not None:
        (folder / "transcribe_ui.mo").write_bytes(compiled)
    monkeypatch.setattr(catalog, "files", lambda _package: tmp_path)
    assert catalog.load_translations(HUNGARIAN).gettext("Retry") == "Retry"


@pytest.mark.parametrize(
    ("environ", "language"),
    [
        ({LANGUAGE: "hu:en_US", LANG: US_ENGLISH}, HUNGARIAN),
        ({LANGUAGE: "en_GB:hu", LANG: HUNGARIAN_LOCALE}, ENGLISH),
        ({LANGUAGE: "", LC_ALL: "hu_HU.UTF-8@euro", LANG: US_ENGLISH}, HUNGARIAN),
        ({"LC_MESSAGES": "hu_HU", LANG: US_ENGLISH}, HUNGARIAN),
        ({LANG: HUNGARIAN_LOCALE}, HUNGARIAN),
        ({LANG: "de_DE.UTF-8"}, GERMAN),
        ({LC_ALL: "C", LANG: HUNGARIAN_LOCALE}, ENGLISH),
        ({LC_ALL: "C.UTF-8"}, ENGLISH),
        ({LANG: "POSIX"}, ENGLISH),
        ({}, None),
    ],
)
def test_posix_language_reads_the_first_set_variable(environ: dict[str, str], language: str | None) -> None:
    """`LANGUAGE` (its first entry), `LC_ALL`, `LC_MESSAGES`, `LANG`: the first set one decides; C and POSIX are English."""
    assert posix_language(environ) == language


def _raise_os_error() -> int:
    raise OSError


@pytest.mark.parametrize(
    ("language_id", "language"),
    [(HUNGARIAN_ID, HUNGARIAN), (ENGLISH_ID, ENGLISH), (GERMAN_ID, GERMAN), (UNKNOWN_ID, None)],
)
def test_windows_language_reads_the_ui_language(language_id: int, language: str | None) -> None:
    """`GetUserDefaultUILanguage`'s LANGID gives its language; an unknown LANGID gives none."""
    assert windows_language(lambda: language_id) == language


def test_windows_language_survives_a_failed_call() -> None:
    """A failed call gives no language instead of an error."""
    assert windows_language(_raise_os_error) is None


def _raise_timeout() -> str:
    raise subprocess.TimeoutExpired(DEFAULTS_COMMAND, DEFAULTS_TIMEOUT_S)


@pytest.mark.parametrize(
    ("printed", "language"),
    [
        (APPLE_HUNGARIAN, HUNGARIAN),
        ("(\n    en,\n    hu\n)\n", ENGLISH),
        ('(\n    "de-DE"\n)\n', GERMAN),
        ("()\n", None),
    ],
)
def test_apple_language_reads_the_first_entry(printed: str, language: str | None) -> None:
    """The first entry of AppleLanguages decides, quoted or not; an empty list gives none."""
    assert apple_language(lambda: printed) == language


def test_apple_language_survives_a_failed_command() -> None:
    """A `defaults` command that fails or hangs gives no language."""
    assert apple_language(_raise_timeout) is None


@pytest.mark.parametrize(
    ("platform", "language_id", "printed", "environ"),
    [
        ("win32", HUNGARIAN_ID, APPLE_ENGLISH, {LANG: US_ENGLISH}),
        ("darwin", ENGLISH_ID, APPLE_HUNGARIAN, {LANG: US_ENGLISH}),
        ("linux", ENGLISH_ID, APPLE_ENGLISH, {LANG: HUNGARIAN_LOCALE}),
    ],
)
def test_each_platform_uses_its_own_source(
    platform: str, language_id: int, printed: str, environ: dict[str, str]
) -> None:
    """Windows reads the LANGID, macOS AppleLanguages, every other system the environment."""
    assert platform_language(platform, environ, lambda: language_id, lambda: printed) == HUNGARIAN


def _store(tmp_path: Path, text: str | None) -> SettingsStore:
    path = tmp_path / "settings.json"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    return SettingsStore(path)


@pytest.mark.parametrize(
    ("text", "setting"),
    [
        (None, SYSTEM),
        ("not json", SYSTEM),
        ("[1, 2]", SYSTEM),
        ('{"language": "fr"}', SYSTEM),
        ('{"language": 3}', SYSTEM),
        ('{"include_hidden": "maybe", "language": "hu"}', HUNGARIAN),
        ('{"enabled_languages": []}', SYSTEM),
    ],
)
def test_an_invalid_settings_file_gives_system(tmp_path: Path, text: str | None, setting: LanguageSetting) -> None:
    """No file, an unreadable or invalid one, or an invalid language: `system`; another broken field does not matter."""
    assert _store(tmp_path, text).language() == setting


@pytest.mark.parametrize("setting", [SYSTEM, ENGLISH, HUNGARIAN])
def test_the_saved_language_is_read_back(tmp_path: Path, setting: LanguageSetting) -> None:
    """A saved setting is what the frontends start with, read through the backend."""
    ports = seeded_ports(tmp_path)
    ports.state.settings.save(default_settings().model_copy(update={"language": setting}))
    assert Backend(ports).language_setting() == setting
