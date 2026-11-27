"""The main window and the wiring of the bus (architecture §3.1 "GUI").

Areas register their page here (`add_page` replaces the placeholder of its key) and connect the
action signals they handle with `connect_handler` (one line each, in `build_shell`). The window
decides no business rule: errors and confirmations come from the backend, and the close
question (Q30, `gui/closing.py`) sends no request.
"""

from collections.abc import Callable
from functools import partial
from typing import override

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMainWindow, QVBoxLayout, QWidget

from transcribe_ui.gui import constants
from transcribe_ui.gui.activity_view import ActivityPanel
from transcribe_ui.gui.closing import CloseGuard
from transcribe_ui.gui.context import GuiContext
from transcribe_ui.gui.dialogs import ConfirmationDialog, ErrorDialog
from transcribe_ui.gui.feedback import ConfirmationView, ErrorView, Fix
from transcribe_ui.gui.pages import PageArea, placeholder
from transcribe_ui.gui.signals import ACTION_SIGNALS, StartupSlot
from transcribe_ui.gui.texts import page_title
from transcribe_ui.i18n.catalog import tr


class MainWindow(QMainWindow):
    """Left navigation of the four pages (Q33), the page area and the activity panel."""

    def __init__(self, context: GuiContext) -> None:
        """Build the shell with a placeholder per page."""
        super().__init__()
        self.context = context
        self.setWindowTitle(constants.WINDOW_TITLE)
        self.pages = PageArea(context.lock, self)
        self.activity_panel = ActivityPanel(context.activity, self.show_error, self)
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.addWidget(self.pages, 1)
        layout.addWidget(self.activity_panel)
        self.setCentralWidget(central)
        self.closing = CloseGuard(self, context.executor, context.quit_now, context.run_active)
        self.dialog: ErrorDialog | ConfirmationDialog | None = None
        self._handled: set[str] = set()
        for key, needs_database in constants.PAGES:
            title = page_title(key)
            self.pages.add_page(key, title, placeholder(self), needs_database=needs_database)

    def connect_handler(self, signal_name: str, slot: Callable[..., object]) -> None:
        """Connect the handler of an area to the action signal `signal_name` (`ACTION_SIGNALS`)."""
        self._handled.add(signal_name)
        getattr(self.context.signals, signal_name).connect(slot)

    def connect_fallbacks(self) -> None:
        """Answer every action signal without a handler with "Not available yet"."""
        for signal_name in ACTION_SIGNALS:
            fallback = partial(self._unhandled, signal_name)
            getattr(self.context.signals, signal_name).connect(fallback)

    def show_error(self, view: ErrorView, retry: Callable[[], object] | None = None) -> ErrorDialog:
        """Open the error dialog; its fix opens a page, or (`app_busy`) calls `retry` and probes the lock."""
        fix = partial(self._apply_fix, retry)
        dialog = ErrorDialog(view, self.context.log_dir, fix, self)
        self.dialog = dialog
        dialog.open()
        return dialog

    def show_confirmation(self, view: ConfirmationView, on_confirm: Callable[[], None]) -> ConfirmationDialog:
        """Open the confirmation dialog; Confirm calls `on_confirm` (which re-sends the confirmed request)."""
        dialog = ConfirmationDialog(view, self)
        dialog.accepted.connect(on_confirm)
        self.dialog = dialog
        dialog.open()
        return dialog

    def busy(self) -> bool:
        """Return whether a call has not completed yet."""
        return self.context.executor.pending() > 0

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        """Close at once when nothing is in flight; otherwise ask (Q30), stop the calls and wait."""
        if self.closing.allows_close():
            event.accept()
        else:
            event.ignore()

    def _unhandled(self, signal_name: str, *_arguments: object) -> None:
        if signal_name not in self._handled:
            self.statusBar().showMessage(tr("Not available yet."), constants.STATUS_MESSAGE_MS)

    def _apply_fix(self, retry: Callable[[], object] | None, fix: Fix) -> None:
        signals = self.context.signals
        if fix.kind == "accounts":
            signals.open_settings.emit(constants.SECTION_ACCOUNTS)
        elif fix.kind == "cloud_setup":
            signals.open_settings.emit(constants.SECTION_CLOUD_SETUP)
        elif fix.kind == "open_batch":
            signals.open_job.emit(fix.argument)
        else:
            # `app_busy`: re-send the failed request (if any) and probe the writer lock, so the
            # lock notice also learns when the other process has let go.
            if retry is not None:
                retry()
            self.context.lock.retry()


def _connect_navigation(window: MainWindow) -> None:
    signals = window.context.signals
    pages = window.pages
    signals.open_job.connect(lambda _batch_id: pages.show_page(constants.PAGE_JOBS))
    signals.open_jobs.connect(partial(pages.show_page, constants.PAGE_JOBS))
    signals.open_new_job.connect(partial(pages.show_page, constants.PAGE_NEW_JOB))
    signals.open_cloud.connect(partial(pages.show_page, constants.PAGE_CLOUD))
    signals.open_cloud_machines.connect(partial(pages.show_page, constants.PAGE_CLOUD))
    signals.open_settings.connect(lambda _section: pages.show_page(constants.PAGE_SETTINGS))


def build_shell(context: GuiContext) -> MainWindow:
    """Build the window and wire the bus: navigation, fallbacks, the start-up probe of the writer lock."""
    window = MainWindow(context)
    _connect_navigation(window)
    window.connect_fallbacks()
    context.executor.observe(context.lock.note)
    context.hooks.register(StartupSlot.DATABASE, context.lock.probe)
    context.lock.when_acquired(partial(context.signals.batches_changed.emit, ()))
    context.lock.when_acquired(context.signals.runs_changed.emit)
    return window
