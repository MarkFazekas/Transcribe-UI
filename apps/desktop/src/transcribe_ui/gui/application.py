"""The `QApplication` of the GUI, created once with the window icon (docs/ai/plans/m8.md, M8.3)."""

from PySide6.QtWidgets import QApplication

from transcribe_ui.gui.constants import QT_APPLICATION_ARGUMENTS
from transcribe_ui.gui.icon import app_icon


def application() -> QApplication:
    """Return the running `QApplication`, creating it (with the window icon) on the first call."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    app = QApplication(list(QT_APPLICATION_ARGUMENTS))
    app.setWindowIcon(app_icon())
    return app
