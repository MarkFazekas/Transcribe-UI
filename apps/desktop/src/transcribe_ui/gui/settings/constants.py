"""Constants of the settings area: the settings page, accounts, cloud setup, about and the first start."""

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

from transcribe_ui.api.classes import LanguageSetting
from transcribe_ui.credentials.classes import CredentialName
from transcribe_ui.gui.constants import SECTION_ACCOUNTS, SECTION_CLOUD_SETUP
from transcribe_ui.placement.classes import PlacementStrategy
from transcribe_ui.runpod.classes import CloudObjectState

# Whether the stored settings are loaded: not yet, loaded, or the load failed (nothing to edit).
type LoadState = Literal["loading", "ready", "failed"]
# What the window's start-up notice asks for: accounts, the cloud setup, or a Retry of the cloud check.
type NoticeKind = Literal["accounts", "cloud_setup", "cloud_error"]
# A page of the first-start set-up wizard.
type WizardStep = Literal["accounts", "cloud_setup"]

# Settings section of the preferences (languages, file types, ffmpeg, placement, language).
SECTION_PREFERENCES: Final = "preferences"
# Settings section of the app's version and log folder.
SECTION_ABOUT: Final = "about"
# The settings page's sections (tabs) in order; `open_settings("")` opens the first.
SECTIONS: Final = (SECTION_PREFERENCES, SECTION_ACCOUNTS, SECTION_CLOUD_SETUP, SECTION_ABOUT)

# Choices of the language of the texts, in the order the settings page lists them.
LANGUAGE_CHOICES: Final[tuple[LanguageSetting, ...]] = ("system", "en", "hu")
# Names of the fixed languages in their own language (never translated).
LANGUAGE_NATIVE_NAMES: Final[Mapping[LanguageSetting, str]] = MappingProxyType({"en": "English", "hu": "Magyar"})
# Placement strategies the defaults offer, in the order the settings page lists them.
PLACEMENT_STRATEGIES: Final[tuple[PlacementStrategy, ...]] = ("next_to_media", "mirror", "flat", "flat_with_folders")
# Largest number of folder names the settings page offers for `flat_with_folders`.
MAX_FOLDER_COUNT: Final = 99
# Column of the language list showing the language's name and its on/off box.
NAME_COLUMN: Final = 0
# Column of the language list showing the language's code.
CODE_COLUMN: Final = 1
# Column of the language list showing whether the language has word timing (alignment).
ALIGNMENT_COLUMN: Final = 2
# Number of columns of the language list.
LANGUAGE_COLUMNS: Final = 3
# Least height of the language list, in pixels (about ten rows).
LANGUAGE_TABLE_HEIGHT: Final = 300
# Number of columns of the cloud setup's object list (object, name, state).
CLOUD_COLUMNS: Final = 3
# Separators between the extensions typed into one field (spaces, commas, semicolons).
EXTENSION_SEPARATORS: Final = re.compile(r"[\s,;]+")
# Separator the settings page shows the extensions with.
EXTENSION_JOIN: Final = " "

# Location prefix of a settings field in an `invalid_request` problem of `update_settings`.
SETTINGS_LOCATION: Final = ("update_settings", "settings")
# Location prefix of a credential field in an `invalid_request` problem of `store_credentials`.
CREDENTIALS_LOCATION: Final = ("store_credentials",)
# Field of a nested settings model whose problems are shown at its own fields (`placement.<field>`).
NESTED_FIELD: Final = "placement"
# Key of a problem of the whole document (no field), shown above the Save button.
FORM_FIELD: Final = ""
# The preferences fields that show how to fix them under the field (others: above the Save button).
ERROR_FIELDS: Final = (
    "enabled_languages",
    "video_extensions",
    "audio_extensions",
    "ffmpeg_path",
    "ffprobe_path",
    "output_folder",
    "folder_count",
    FORM_FIELD,
)

# Key of the activity panel entry of `setup_cloud` (one at a time).
SETUP_CLOUD_KEY: Final = "setup_cloud"
# Object states that open the first-start wizard and the notice (`duplicated` is left to the setup page).
SETUP_NEEDED_STATES: Final[frozenset[CloudObjectState]] = frozenset(("missing", "outdated"))
# The credential an installation may leave unset (R2-only role, architecture §3.9).
OPTIONAL_CREDENTIAL: Final[CredentialName] = "runpod_api_key"

# Environment variable that makes a restarted one-file build unpack itself again (PyInstaller >= 6.10).
PYINSTALLER_RESET_VARIABLE: Final = "PYINSTALLER_RESET_ENVIRONMENT"
# Code the interpreter runs to restart the GUI from source (no `__main__` module needed).
RELAUNCH_SOURCE: Final = "from transcribe_ui.gui.main import run; run()"
