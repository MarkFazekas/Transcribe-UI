"""The About section: the versions, the log folder and the third-party licences dialog."""

from functools import partial
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from transcribe_ui.gui.constants import WINDOW_TITLE
from transcribe_ui.gui.dialogs import open_folder, wrapped_label
from transcribe_ui.gui.settings.about import AboutViewModel
from transcribe_ui.gui.settings.cloud_view import ShowError
from transcribe_ui.gui.settings.constants import NOTICES_DIALOG_SIZE
from transcribe_ui.gui.settings.widgets import show_text
from transcribe_ui.i18n.catalog import tr


def _selectable(label: QLabel) -> QLabel:
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _log_button(log_dir: Path, parent: QWidget) -> QPushButton:
    button = QPushButton(tr("Open log folder"), parent)
    button.clicked.connect(partial(open_folder, log_dir))
    return button


def _buttons_row(log_button: QPushButton, notices_button: QPushButton) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(log_button)
    row.addWidget(notices_button)
    row.addStretch()
    return row


def _notices_edit(parent: QWidget) -> QPlainTextEdit:
    edit = QPlainTextEdit(parent)
    edit.setReadOnly(True)
    edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    edit.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
    return edit


class NoticesDialog(QDialog):
    """The third-party licence notices, read-only; while loading, from source or failed, a line instead."""

    def __init__(self, model: AboutViewModel, show_error: ShowError, parent: QWidget | None = None) -> None:
        """Follow `model`; Details shows a failed load's error through `show_error`."""
        super().__init__(parent)
        self._model = model
        self._show_error = show_error
        self.setWindowTitle(tr("Third-party licences"))
        self.resize(*NOTICES_DIALOG_SIZE)
        self.message_label = wrapped_label("", self)
        self.text_edit = _notices_edit(self)
        self.details_button = QPushButton(tr("Details"), self)
        self.details_button.clicked.connect(self._details)
        self._lay_out()
        model.subscribe(self._repaint)
        self._repaint()

    def show_notices(self) -> None:
        """Open the dialog (window-modal) and load the notices unless they are loaded."""
        self._model.load_notices()
        self.open()

    def _lay_out(self) -> None:
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(self.details_button)
        row.addStretch()
        row.addWidget(buttons)
        layout = QVBoxLayout(self)
        layout.addWidget(self.message_label)
        layout.addWidget(self.text_edit, 1)
        layout.addLayout(row)

    def _details(self) -> None:
        if self._model.notices_error is not None:
            self._show_error(self._model.notices_error)

    def _repaint(self) -> None:
        model = self._model
        message = model.notices_message()
        show_text(self.message_label, message)
        notices = model.notices if model.notices_state == "loaded" else None
        if notices is not None and self.text_edit.toPlainText() != notices:
            self.text_edit.setPlainText(notices)
        self.text_edit.setVisible(notices is not None)
        self.details_button.setVisible(model.notices_error is not None)


class AboutView(QWidget):
    """The versions, the log folder and the third-party licences, bound to an `AboutViewModel`."""

    def __init__(self, model: AboutViewModel, show_error: ShowError, parent: QWidget | None = None) -> None:
        """Build the section and follow `model`; a failed load of the licences shows through `show_error`."""
        super().__init__(parent)
        self._model = model
        self.version_label = _selectable(QLabel(self))
        self.notices_dialog = NoticesDialog(model, show_error, self)
        self.notices_button = QPushButton(tr("Third-party licences"), self)
        self.notices_button.clicked.connect(self.notices_dialog.show_notices)
        self._lay_out()
        model.subscribe(self._repaint)
        self._repaint()

    def _lay_out(self) -> None:
        log_dir = self._model.log_dir
        log_folder = tr("Log folder: {folder}").format(folder=log_dir)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(WINDOW_TITLE, self))
        layout.addWidget(self.version_label)
        layout.addWidget(_selectable(wrapped_label(log_folder, self)))
        layout.addLayout(_buttons_row(_log_button(log_dir, self), self.notices_button))
        layout.addStretch()

    def _repaint(self) -> None:
        model = self._model
        if not model.version:
            self.version_label.setText("")
            return
        versions = tr("Version {version}, data format {schema}")
        shown = versions.format(version=model.version, schema=model.schema_version)
        self.version_label.setText(shown)
