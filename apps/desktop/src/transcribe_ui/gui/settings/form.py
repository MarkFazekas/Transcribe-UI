"""The widgets of the preferences form and their groups (languages, file types, ffmpeg, downloads, language)."""

from dataclasses import dataclass
from functools import partial

from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QGroupBox, QLabel, QLineEdit, QSpinBox, QWidget

from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.settings.constants import ERROR_FIELDS, LANGUAGE_CHOICES, MAX_FOLDER_COUNT, PLACEMENT_STRATEGIES
from transcribe_ui.gui.settings.languages_view import LanguageTable, Toggle
from transcribe_ui.gui.settings.texts import language_choice, strategy_label
from transcribe_ui.gui.settings.widgets import PathField, choose_folder, choose_program, error_line
from transcribe_ui.i18n.catalog import tr

# The line under each field that says how to fix it, by field key (the `SettingsDraft` attribute names).
type ErrorLines = dict[str, QLabel]


@dataclass(frozen=True)
class PreferenceFields:
    """The editable widgets of the preferences form."""

    languages: LanguageTable
    video: QLineEdit
    audio: QLineEdit
    hidden: QCheckBox
    ffmpeg: PathField
    ffprobe: PathField
    strategy: QComboBox
    output_folder: PathField
    folder_count: QSpinBox
    overwrite: QCheckBox
    language: QComboBox


def _strategy_box(parent: QWidget) -> QComboBox:
    box = QComboBox(parent)
    for strategy in PLACEMENT_STRATEGIES:
        box.addItem(strategy_label(strategy), strategy)
    return box


def _language_box(parent: QWidget) -> QComboBox:
    box = QComboBox(parent)
    for choice in LANGUAGE_CHOICES:
        box.addItem(language_choice(choice), choice)
    return box


def _count_box(parent: QWidget) -> QSpinBox:
    box = QSpinBox(parent)
    box.setRange(1, MAX_FOLDER_COUNT)
    return box


def _program_field(parent: QWidget, title: str) -> PathField:
    return PathField(tr("The bundled program"), partial(choose_program, title), parent)


def make_fields(parent: QWidget, on_toggle: Toggle) -> PreferenceFields:
    """Return the form's widgets; `on_toggle` hears the language boxes."""
    folder = partial(choose_folder, tr("Choose the output folder"))
    return PreferenceFields(
        languages=LanguageTable(on_toggle, parent),
        video=QLineEdit(parent),
        audio=QLineEdit(parent),
        hidden=QCheckBox(tr("Include hidden and system files and folders"), parent),
        ffmpeg=_program_field(parent, tr("Choose the ffmpeg program")),
        ffprobe=_program_field(parent, tr("Choose the ffprobe program")),
        strategy=_strategy_box(parent),
        output_folder=PathField(tr("Not chosen"), folder, parent),
        folder_count=_count_box(parent),
        overwrite=QCheckBox(tr("Replace existing subtitle files"), parent),
        language=_language_box(parent),
    )


def make_error_lines(parent: QWidget) -> ErrorLines:
    """Return a hidden error line for each field that can be refused, and for the whole form."""
    return {field: error_line(parent) for field in ERROR_FIELDS}


def _group(title: str, note: str, parent: QWidget) -> tuple[QGroupBox, QFormLayout]:
    group = QGroupBox(title, parent)
    rows = QFormLayout(group)
    rows.addRow(wrapped_label(note, group))
    return group, rows


def _add(rows: QFormLayout, label: str, field: QWidget, error: QLabel) -> None:
    rows.addRow(label, field)
    rows.addRow("", error)


def languages_group(fields: PreferenceFields, errors: ErrorLines, parent: QWidget) -> QGroupBox:
    """Return the group of the languages offered in choosers."""
    note = tr(
        "The languages offered when you choose what to transcribe. Word timing: the subtitles get "
        "word-level timing in these languages.",
    )
    group, rows = _group(tr("Languages"), note, parent)
    rows.addRow(fields.languages)
    rows.addRow(errors["enabled_languages"])
    return group


def files_group(fields: PreferenceFields, errors: ErrorLines, parent: QWidget) -> QGroupBox:
    """Return the group of the media extensions and hidden entries."""
    group, rows = _group(tr("File types"), tr("Extensions without the dot, separated by spaces."), parent)
    _add(rows, tr("Video files"), fields.video, errors["video_extensions"])
    _add(rows, tr("Audio files"), fields.audio, errors["audio_extensions"])
    rows.addRow(fields.hidden)
    return group


def tools_group(fields: PreferenceFields, errors: ErrorLines, parent: QWidget) -> QGroupBox:
    """Return the group of the ffmpeg and ffprobe overrides."""
    group, rows = _group("ffmpeg", tr("Leave a field empty to use the bundled program."), parent)
    _add(rows, "ffmpeg", fields.ffmpeg, errors["ffmpeg_path"])
    _add(rows, "ffprobe", fields.ffprobe, errors["ffprobe_path"])
    return group


def placement_group(fields: PreferenceFields, errors: ErrorLines, parent: QWidget) -> QGroupBox:
    """Return the group of the download defaults."""
    group, rows = _group(tr("Download defaults"), tr("The download dialog starts with these choices."), parent)
    rows.addRow(tr("Place subtitles"), fields.strategy)
    _add(rows, tr("Output folder"), fields.output_folder, errors["output_folder"])
    _add(rows, tr("Folder names in the file name"), fields.folder_count, errors["folder_count"])
    rows.addRow(fields.overwrite)
    return group


def language_group(fields: PreferenceFields, parent: QWidget) -> QGroupBox:
    """Return the group of the language of the texts."""
    group, rows = _group(tr("Language of the app"), tr("A change applies after a restart."), parent)
    rows.addRow(tr("Language"), fields.language)
    return group
