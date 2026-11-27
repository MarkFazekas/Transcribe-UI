"""The page area of the main window: left navigation, the pages, and the writer-lock notice.

Each area registers its page under its key (`add_page` replaces the placeholder registered by
the shell). While another process holds the writer lock, a page that needs the database is
replaced by the notice with Retry; pages that need none (Settings) stay usable.
"""

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from transcribe_ui.gui.constants import APP_BUSY_TEXT, APP_BUSY_TITLE, PLACEHOLDER_TEXT
from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.writer_lock import WriterLockGate


class LockNotice(QWidget):
    """Shown instead of the pages that need the database while another process holds the writer lock."""

    def __init__(self, lock: WriterLockGate, parent: QWidget | None = None) -> None:
        """Bind to the gate; Retry probes again."""
        super().__init__(parent)
        self._lock = lock
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(APP_BUSY_TITLE, self))
        layout.addWidget(wrapped_label(APP_BUSY_TEXT, self))
        self.message = wrapped_label(lock.message, self)
        layout.addWidget(self.message)
        self.retry_button = QPushButton("Retry", self)
        self.retry_button.clicked.connect(lock.retry)
        layout.addWidget(self.retry_button)
        layout.addStretch()
        lock.subscribe(self._refresh)

    def _refresh(self) -> None:
        self.message.setText(self._lock.message)


def placeholder(parent: QWidget) -> QWidget:
    """Return the page shown until the part that builds a page registers it."""
    label = QLabel(PLACEHOLDER_TEXT, parent)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


@dataclass(frozen=True)
class _Page:
    widget: QWidget
    needs_database: bool


class PageArea(QWidget):
    """The navigation list and the visible page (or the writer-lock notice)."""

    def __init__(self, lock: WriterLockGate, parent: QWidget | None = None) -> None:
        """Start without pages; the notice follows `lock`."""
        super().__init__(parent)
        self._lock = lock
        self.navigation = QListWidget(self)
        self.stack = QStackedWidget(self)
        self.notice = LockNotice(lock, self)
        self._body = QStackedWidget(self)
        self._body.addWidget(self.stack)
        self._body.addWidget(self.notice)
        layout = QHBoxLayout(self)
        layout.addWidget(self.navigation, 1)
        layout.addWidget(self._body, 4)
        self._pages: dict[str, _Page] = {}
        self._order: list[str] = []
        self.navigation.currentRowChanged.connect(self._on_row)
        lock.subscribe(self._update)

    def add_page(self, key: str, title: str, widget: QWidget, *, needs_database: bool) -> None:
        """Register a page under `key` (replacing the page or placeholder registered before)."""
        previous = self._pages.get(key)
        self._pages[key] = _Page(widget, needs_database)
        if previous is None:
            self._order.append(key)
            self.stack.addWidget(widget)
            self.navigation.addItem(title)
            if self.navigation.currentRow() < 0:
                self.navigation.setCurrentRow(0)
            return
        index = self.stack.indexOf(previous.widget)
        self.stack.removeWidget(previous.widget)
        previous.widget.deleteLater()
        self.stack.insertWidget(index, widget)
        self.stack.setCurrentIndex(self.navigation.currentRow())

    def page(self, key: str) -> QWidget:
        """Return the page registered under `key`."""
        return self._pages[key].widget

    def current_page(self) -> str:
        """Return the key of the selected page."""
        return self._order[self.navigation.currentRow()]

    def show_page(self, key: str) -> None:
        """Select the page of `key`."""
        self.navigation.setCurrentRow(self._order.index(key))

    def notice_shown(self) -> bool:
        """Return whether the writer-lock notice is shown instead of the selected page."""
        return self._body.currentWidget() is self.notice

    def _on_row(self, row: int) -> None:
        if row >= 0:
            self.stack.setCurrentIndex(row)
            self._update()

    def _update(self) -> None:
        if self.navigation.currentRow() < 0:
            return
        needs_database = self._pages[self.current_page()].needs_database
        busy = self._lock.state == "busy" and needs_database
        self._body.setCurrentWidget(self.notice if busy else self.stack)
