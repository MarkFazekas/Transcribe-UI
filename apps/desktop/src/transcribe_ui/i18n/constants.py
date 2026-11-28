"""Constants of the frontends' texts (architecture §3.1 "Language of the texts")."""

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

# A language the app has texts in.
type Language = Literal["en", "hu"]

# gettext domain of the catalogs: `locale/<language>/LC_MESSAGES/<domain>.mo`.
DOMAIN: Final = "transcribe_ui"
# Package whose `locale` folder holds the catalogs (package data, so the one-file builds carry them).
LOCALE_PACKAGE: Final = "transcribe_ui"
# Folder of the catalogs in that package, one sub-folder per language.
LOCALE_FOLDER: Final = "locale"
# Folder of a language's catalog below its language folder.
MESSAGES_FOLDER: Final = "LC_MESSAGES"
# Extension of a compiled catalog.
COMPILED_SUFFIX: Final = ".mo"
# Language of the msgids, and of the texts when the OS language is not one the app has.
DEFAULT_LANGUAGE: Final[Language] = "en"
# Language of the Hungarian catalog.
HUNGARIAN: Final[Language] = "hu"

# Environment variables naming the POSIX language, in the order gettext reads them.
POSIX_LANGUAGE_VARIABLES: Final = ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")
# The variable that holds a `:`-separated list of languages, of which the first counts.
LANGUAGE_LIST_VARIABLE: Final = "LANGUAGE"
# Separator of the languages in `LANGUAGE`.
LANGUAGE_LIST_SEPARATOR: Final = ":"
# Ends the language part of a locale name: `hu_HU.UTF-8@euro` (POSIX) or `hu-HU` (Apple).
LOCALE_NAME_END: Final = re.compile(r"[_.@-]")
# Locale names (lower case, cut like a language) that mean the untranslated texts: English.
ENGLISH_LOCALES: Final = frozenset(("c", "posix"))
# The macOS command that prints the user's preferred languages, first the UI language.
APPLE_LANGUAGES_COMMAND: Final = ("/usr/bin/defaults", "read", "-g", "AppleLanguages")
# Seconds the macOS command may take before the language counts as unknown.
APPLE_LANGUAGES_TIMEOUT_S: Final = 5
# Characters around an entry of the printed AppleLanguages list (`(`, `)`, quotes, commas, spaces).
APPLE_LIST_PUNCTUATION: Final = '()", \t'

# Separator of a variant's key in the code tables: `<code>.<value of its details key>`.
VARIANT_SEPARATOR: Final = "."
# Separator of the entries of a list shown in a text.
LIST_SEPARATOR: Final = ", "
# A fixed-point format spec of a number in a text (`{cost:.2f}`), with its decimals.
DECIMALS_SPEC: Final = re.compile(r"\.(\d+)f")
# Format spec of a number of seconds shown as a duration (`{audio_s:duration}`).
DURATION_SPEC: Final = "duration"
# Seconds per minute and per hour, for durations.
SECONDS_PER_MINUTE: Final = 60
# Seconds per hour.
SECONDS_PER_HOUR: Final = 3600
# Thousands separator of Hungarian numbers (a no-break space, so a number never wraps).
HUNGARIAN_GROUP_SEPARATOR: Final = "\u00a0"

# A kind of backend code the frontends show a text for.
type TextKind = Literal[
    "error",
    "confirmation",
    "plan_error",
    "download_failure",
    "rename_reason",
    "check",
    "result_error",
    "scan_warning",
    "item_problem",
    "price_source",
    "stage",
    "batch_phase",
    "batch_stage",
    "batch_label",
    "item_phase",
    "job_status",
    "job_kind",
]
# Error codes whose wording depends on one detail, and that detail's key (entries `<code>.<value>`).
ERROR_VARIANTS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "media_tool_not_found": "source",
        "media_tool_failed": "operation",
        "media_output_unwritable": "operation",
        "choice_invalid": "reason",
        "cloud_unreachable": "part",
        "cloud_setup_interrupted": "reason",
        "credentials_missing": "keyring",
    },
)
# Plan error codes whose wording depends on one detail, and that detail's key.
PLAN_ERROR_VARIANTS: Final[Mapping[str, str]] = MappingProxyType({"custom_plan_invalid_name": "problem"})
