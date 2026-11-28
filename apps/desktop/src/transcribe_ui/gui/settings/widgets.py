"""Small widgets of the settings area: a field's error line, a path field with Browse, text updates."""

from collections.abc import Callable

from PySide6.QtCore import QDir
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.i18n.catalog import tr

# Asks the user for a path (a file dialog); None when cancelled. Tests replace it.
type Chooser = Callable[[QWidget], str | None]


def show_text(label: QLabel, text: str) -> None:
    """Show `text` in `label`, hiding the label while the text is empty."""
    if label.text() != text:
        label.setText(text)
    label.setVisible(bool(text))


def set_field_text(field: QLineEdit, text: str) -> None:
    """Set a field's text only when it differs, so the cursor does not jump while the user types."""
    if field.text() != text:
        field.setText(text)


def error_line(parent: QWidget) -> QLabel:
    """Return the hidden line that shows how to fix a field."""
    label = wrapped_label("", parent)
    label.setVisible(False)
    return label


def native(path: str) -> str | None:
    """Return a path from a Qt file dialog with this OS's separators; None when the dialog was cancelled."""
    return QDir.toNativeSeparators(path) if path else None


def choose_program(title: str, parent: QWidget) -> str | None:
    """Ask for a program file in a file dialog titled `title`; None when cancelled."""
    path, _filter = QFileDialog.getOpenFileName(parent, title)
    return native(path)


def choose_folder(title: str, parent: QWidget) -> str | None:
    """Ask for a folder in a folder dialog titled `title`; None when cancelled."""
    return native(QFileDialog.getExistingDirectory(parent, title))


class PathField(QWidget):
    """A path's text field and a Browse button; empty means none (the field's placeholder says what)."""

    def __init__(self, placeholder: str, chooser: Chooser, parent: QWidget | None = None) -> None:
        """Show `placeholder` while empty; Browse asks `chooser`."""
        super().__init__(parent)
        self.chooser = chooser
        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setClearButtonEnabled(True)
        self.browse_button = QPushButton(tr("Browse..."), self)
        self.browse_button.clicked.connect(self._browse)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.browse_button)

    def _browse(self) -> None:
        chosen = self.chooser(self)
        if chosen is not None:
            # `setText` emits `textChanged`, which the form follows.
            self.edit.setText(chosen)
