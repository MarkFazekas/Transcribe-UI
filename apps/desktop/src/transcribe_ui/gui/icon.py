"""The window icon of the app, built from the PNGs bundled as package data (`gui/icons/`).

The bytes are read through `importlib.resources`, so it works from the source tree, an installed
wheel and the one-file bundle alike. `scripts/build_icon.py` generates the PNGs.
"""

from importlib.resources import files

from PySide6.QtGui import QIcon, QPixmap

from transcribe_ui.gui.constants import ICON_FILE_PATTERN, ICON_FOLDER, ICON_PACKAGE, ICON_SIZES


def _pixmap(size: int) -> QPixmap:
    name = ICON_FILE_PATTERN.format(size=size)
    png = files(ICON_PACKAGE).joinpath(ICON_FOLDER, name).read_bytes()
    pixmap = QPixmap()
    if not pixmap.loadFromData(png):
        message = f"cannot load the icon {name}"
        raise ValueError(message)
    return pixmap


def app_icon() -> QIcon:
    """Return the icon with one pixmap per bundled size (needs a running `QGuiApplication`)."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(_pixmap(size))
    return icon
