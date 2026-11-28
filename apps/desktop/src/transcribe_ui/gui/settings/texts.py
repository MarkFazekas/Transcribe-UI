"""Texts of the settings page's preferences and of the language restart (architecture §3.1 "Language of the texts").

Qt-free. Each text is translated when it is shown (`tr`), never at import time.
"""

from transcribe_ui.api.classes import LanguageSetting
from transcribe_ui.discovery.constants import SIDECAR_EXTENSIONS
from transcribe_ui.gui.constants import SECTION_ACCOUNTS, SECTION_CLOUD_SETUP
from transcribe_ui.gui.settings.constants import FORM_FIELD, LANGUAGE_NATIVE_NAMES, SECTION_ABOUT, SECTION_PREFERENCES
from transcribe_ui.i18n.catalog import tr
from transcribe_ui.placement.classes import PlacementStrategy


def section_title(section: str) -> str:
    """Return the tab title of a settings section."""
    titles = {
        SECTION_PREFERENCES: tr("Preferences"),
        SECTION_ACCOUNTS: tr("Accounts"),
        SECTION_CLOUD_SETUP: tr("Cloud setup"),
        SECTION_ABOUT: tr("About"),
    }
    return titles[section]


def language_choice(choice: LanguageSetting) -> str:
    """Return how the language setting's choice is listed (the fixed languages in their own language)."""
    if choice == "system":
        return tr("System language")
    return LANGUAGE_NATIVE_NAMES[choice]


def strategy_label(strategy: PlacementStrategy) -> str:
    """Return the label of a placement strategy."""
    labels: dict[PlacementStrategy, str] = {
        "next_to_media": tr("Next to the media file"),
        "mirror": tr("In an output folder, mirroring the folders"),
        "flat": tr("All in one output folder"),
        "flat_with_folders": tr("All in one output folder, with folder names in the file name"),
    }
    return labels[strategy]


def settings_hint(field: str) -> str:
    """Return how to fix a settings field the backend refused (a problem of the whole document: `FORM_FIELD`)."""
    extensions = tr("Use lower-case letters and digits without the dot, each extension only once.")
    paths = tr("Choose the program file (a full path), or leave it empty to use the bundled one.")
    hints = {
        "enabled_languages": tr("Turn on at least one language."),
        "video_extensions": extensions,
        "audio_extensions": extensions,
        "ffmpeg_path": paths,
        "ffprobe_path": paths,
        "output_folder": tr("Choose a folder (a full path), or leave it empty."),
        "folder_count": tr("Enter a whole number of at least 1."),
        FORM_FIELD: tr(
            "Check the extensions: one cannot be both a video and an audio extension, nor a subtitle "
            "extension ({extensions}).",
        ).format(extensions=", ".join(SIDECAR_EXTENSIONS)),
    }
    return hints.get(field, tr("This value is not valid."))


def restart_question() -> str:
    """Return the question asked after a saved change of the language of the texts (Q32)."""
    return tr("Restart now to change the language?")
