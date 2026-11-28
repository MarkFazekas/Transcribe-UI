"""The preferences section of the settings page: the form, its field errors, Save and Undo changes.

The form only binds to `PreferencesViewModel`: every change of a widget becomes a new
`SettingsDraft`, and each repaint sets the widgets from the view-model's state (a widget whose
value is already right is left alone, so typing is never disturbed).
"""

from dataclasses import dataclass, replace
from functools import partial
from typing import Any

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.settings.constants import FORM_FIELD
from transcribe_ui.gui.settings.draft import SettingsDraft
from transcribe_ui.gui.settings.form import (
    ErrorLines,
    PreferenceFields,
    files_group,
    language_group,
    languages_group,
    make_error_lines,
    make_fields,
    placement_group,
    tools_group,
)
from transcribe_ui.gui.settings.viewmodel import PreferencesState, PreferencesViewModel
from transcribe_ui.gui.settings.widgets import set_field_text, show_text
from transcribe_ui.i18n.catalog import tr


@dataclass(frozen=True)
class FormControls:
    """The form's state line and buttons."""

    # Loading, or why the settings cannot be loaded.
    load_label: QLabel
    retry_button: QPushButton
    defaults_button: QPushButton
    message_label: QLabel
    revert_button: QPushButton
    save_button: QPushButton


def _controls(view: QWidget) -> FormControls:
    return FormControls(
        load_label=wrapped_label(tr("Loading the settings..."), view),
        retry_button=QPushButton(tr("Retry"), view),
        defaults_button=QPushButton(tr("Restore defaults"), view),
        message_label=wrapped_label("", view),
        revert_button=QPushButton(tr("Undo changes"), view),
        save_button=QPushButton(tr("Save"), view),
    )


def _scroll_area(fields: PreferenceFields, errors: ErrorLines, parent: QWidget) -> QScrollArea:
    area = QScrollArea(parent)
    area.setWidgetResizable(True)
    inner = QWidget(area)
    groups = QVBoxLayout(inner)
    groups.addWidget(languages_group(fields, errors, inner))
    groups.addWidget(files_group(fields, errors, inner))
    groups.addWidget(tools_group(fields, errors, inner))
    groups.addWidget(placement_group(fields, errors, inner))
    groups.addWidget(language_group(fields, inner))
    area.setWidget(inner)
    return area


def _top_row(controls: FormControls) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(controls.retry_button)
    row.addWidget(controls.defaults_button)
    row.addStretch()
    return row


def _bottom_row(controls: FormControls) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(controls.message_label, 1)
    row.addWidget(controls.revert_button)
    row.addWidget(controls.save_button)
    return row


class PreferencesView(QWidget):
    """The editable settings, bound to a `PreferencesViewModel`."""

    def __init__(self, model: PreferencesViewModel, parent: QWidget | None = None) -> None:
        """Build the form and follow `model`."""
        super().__init__(parent)
        self._model = model
        self._painting = False
        self.fields = make_fields(self, self.toggle_language)
        self.errors = make_error_lines(self)
        self.scroll_area = _scroll_area(self.fields, self.errors, self)
        self.controls = _controls(self)
        self._lay_out()
        self._connect()
        model.subscribe(self._repaint)
        self._repaint()

    def toggle_language(self, code: str, enabled: bool) -> None:  # noqa: FBT001 -- the language table's callback.
        """Turn a language on or off in the form."""
        draft = self._model.shown.draft
        if draft is not None and not self._painting:
            self._model.edit(draft.with_language(code, enabled=enabled))

    def _lay_out(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(self.controls.load_label)
        layout.addLayout(_top_row(self.controls))
        layout.addWidget(self.scroll_area, 1)
        layout.addWidget(self.errors[FORM_FIELD])
        layout.addLayout(_bottom_row(self.controls))

    def _connect(self) -> None:
        fields = self.fields
        # The change signal of each widget, by the `SettingsDraft` field it edits.
        changes = (
            ("video_extensions", fields.video.textEdited),
            ("audio_extensions", fields.audio.textEdited),
            ("include_hidden", fields.hidden.toggled),
            ("ffmpeg_path", fields.ffmpeg.edit.textChanged),
            ("ffprobe_path", fields.ffprobe.edit.textChanged),
            ("output_folder", fields.output_folder.edit.textChanged),
            ("folder_count", fields.folder_count.valueChanged),
            ("overwrite", fields.overwrite.toggled),
        )
        for name, signal in changes:
            signal.connect(partial(self._set, name))
        fields.strategy.currentIndexChanged.connect(partial(self._set_choice, "strategy", fields.strategy))
        fields.language.currentIndexChanged.connect(partial(self._set_choice, "language", fields.language))
        self._connect_controls()

    def _connect_controls(self) -> None:
        model = self._model
        self.controls.retry_button.clicked.connect(self._reload)
        self.controls.defaults_button.clicked.connect(model.restore_defaults)
        self.controls.revert_button.clicked.connect(model.revert)
        self.controls.save_button.clicked.connect(model.save)

    def _reload(self) -> None:
        self._model.load()

    def _set_choice(self, name: str, box: QComboBox, _index: int) -> None:
        self._set(name, box.currentData())

    def _set(self, name: str, typed: object) -> None:
        draft = self._model.shown.draft
        if draft is None or self._painting:
            return
        # The names are `SettingsDraft`'s own fields, and each widget gives its field's type.
        changes: dict[str, Any] = {name: typed}
        self._model.edit(replace(draft, **changes))

    def _repaint(self) -> None:
        self._painting = True
        shown = self._model.shown
        _paint_controls(self.controls, shown)
        self.scroll_area.setVisible(shown.state == "ready")
        if shown.draft is not None:
            _paint_fields(self.fields, shown, shown.draft)
        for field, label in self.errors.items():
            show_text(label, shown.errors.get(field, ""))
        self._painting = False


def _paint_controls(controls: FormControls, shown: PreferencesState) -> None:
    failed = shown.state == "failed"
    loading = tr("Loading the settings...") if shown.state == "loading" else shown.load_error
    show_text(controls.load_label, "" if shown.state == "ready" else loading)
    controls.retry_button.setVisible(failed)
    controls.defaults_button.setVisible(failed)
    controls.defaults_button.setEnabled(not shown.saving)
    controls.message_label.setText(shown.message)
    controls.revert_button.setEnabled(shown.can_save)
    controls.save_button.setEnabled(shown.can_save)


def _paint_fields(fields: PreferenceFields, shown: PreferencesState, draft: SettingsDraft) -> None:
    fields.languages.show_languages(shown.languages, draft.enabled_languages)
    set_field_text(fields.video, draft.video_extensions)
    set_field_text(fields.audio, draft.audio_extensions)
    fields.hidden.setChecked(draft.include_hidden)
    set_field_text(fields.ffmpeg.edit, draft.ffmpeg_path)
    set_field_text(fields.ffprobe.edit, draft.ffprobe_path)
    set_field_text(fields.output_folder.edit, draft.output_folder)
    _paint_choices(fields, draft)


def _paint_choices(fields: PreferenceFields, draft: SettingsDraft) -> None:
    fields.strategy.setCurrentIndex(fields.strategy.findData(draft.strategy))
    fields.folder_count.setValue(draft.folder_count)
    fields.overwrite.setChecked(draft.overwrite)
    fields.language.setCurrentIndex(fields.language.findData(draft.language))
