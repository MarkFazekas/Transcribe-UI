"""The file dialogs of the download dialog, behind a port so tests answer them without a window."""

from typing import Protocol

from PySide6.QtWidgets import QFileDialog, QWidget

from transcribe_ui.i18n.catalog import tr


class FileChooser(Protocol):
    """Asks the user for a folder or a file; None when the user cancelled."""

    def folder(self, parent: QWidget, start: str) -> str | None:
        """Return the chosen output folder."""
        ...

    def save_plan(self, parent: QWidget, start: str) -> str | None:
        """Return the file to export the custom plan to."""
        ...

    def open_plan(self, parent: QWidget, start: str) -> str | None:
        """Return the edited custom plan file to import."""
        ...


def _json_files() -> str:
    return tr("JSON files (*.json)")


class QtFileChooser:
    """The system's file dialogs (Qt's own where the platform has none)."""

    def folder(self, parent: QWidget, start: str) -> str | None:
        """Return the chosen output folder."""
        chosen = QFileDialog.getExistingDirectory(parent, tr("Choose the output folder"), start)
        return chosen or None

    def save_plan(self, parent: QWidget, start: str) -> str | None:
        """Return the file to export the custom plan to (the dialog asks before replacing one)."""
        title = tr("Save the custom plan")
        chosen, _selected_filter = QFileDialog.getSaveFileName(parent, title, start, _json_files())
        return chosen or None

    def open_plan(self, parent: QWidget, start: str) -> str | None:
        """Return the edited custom plan file to import."""
        title = tr("Import the custom plan")
        chosen, _selected_filter = QFileDialog.getOpenFileName(parent, title, start, _json_files())
        return chosen or None
