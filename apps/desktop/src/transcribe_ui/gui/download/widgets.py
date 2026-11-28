"""Small layout helpers of the download dialog's panels."""

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

# Widgets laid out one below the other.
type Widgets = tuple[QWidget, ...]


def show_text(label: QLabel, text: str | None) -> None:
    """Show `text` in the label, or hide the label while there is none."""
    label.setText(text or "")
    label.setVisible(bool(text))


def stacked(parent: QWidget, widgets: Widgets, stretched: QWidget | None = None) -> QVBoxLayout:
    """Return the parent's layout of the widgets one below the other (`stretched` takes the spare height)."""
    layout = QVBoxLayout(parent)
    layout.setContentsMargins(0, 0, 0, 0)
    for widget in widgets:
        stretch = 1 if widget is stretched else 0
        layout.addWidget(widget, stretch)
    return layout
