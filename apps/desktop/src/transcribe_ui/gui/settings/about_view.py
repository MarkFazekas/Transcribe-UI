"""The About section: the app's and the data format's versions, and the log folder."""

from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from transcribe_ui.gui.constants import WINDOW_TITLE
from transcribe_ui.gui.dialogs import open_folder, wrapped_label
from transcribe_ui.gui.settings.about import AboutViewModel
from transcribe_ui.i18n.catalog import tr


def _selectable(label: QLabel) -> QLabel:
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _log_button(model: AboutViewModel, parent: QWidget) -> QHBoxLayout:
    button = QPushButton(tr("Open log folder"), parent)
    button.clicked.connect(partial(open_folder, model.log_dir))
    row = QHBoxLayout()
    row.addWidget(button)
    row.addStretch()
    return row


class AboutView(QWidget):
    """The versions and the log folder, bound to an `AboutViewModel`."""

    def __init__(self, model: AboutViewModel, parent: QWidget | None = None) -> None:
        """Build the section and follow `model`."""
        super().__init__(parent)
        self._model = model
        self.version_label = _selectable(QLabel(self))
        log_folder = tr("Log folder: {folder}").format(folder=model.log_dir)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(WINDOW_TITLE, self))
        layout.addWidget(self.version_label)
        layout.addWidget(_selectable(wrapped_label(log_folder, self)))
        layout.addLayout(_log_button(model, self))
        layout.addStretch()
        model.subscribe(self._repaint)
        self._repaint()

    def _repaint(self) -> None:
        model = self._model
        if not model.version:
            self.version_label.setText("")
            return
        versions = tr("Version {version}, data format {schema}")
        shown = versions.format(version=model.version, schema=model.schema_version)
        self.version_label.setText(shown)
