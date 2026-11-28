"""The Cloud page: the check banners, the current run, the cloud machines and the run history (§3.1 "GUI").

Binds the sections to the view-models and decides nothing. A failed check of the cloud shows a
banner with its fix and Retry; machines that need attention show a banner that leads to the
cloud machines list; without a Runpod key a banner says what this computer can and cannot do,
with a button to the accounts. Each showing of the page asks again whether the key is set.
Other areas may add a section below the current run (`add_section`).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget

from transcribe_ui.gui.cloud.area import CloudModels
from transcribe_ui.gui.cloud.ports import Clock
from transcribe_ui.gui.cloud.run_panel import OpenJob, RunPanel
from transcribe_ui.gui.cloud.sections import CheckBanner, HistorySection, KeyBanner, MachinesSection, OnFix
from transcribe_ui.gui.cloud.start_view import StartDialog
from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.i18n.catalog import tr


@dataclass(frozen=True)
class PageCallbacks:
    """What the page's links and buttons lead to outside the page."""

    open_job: OpenJob
    delete_unknown: Callable[[str], object]
    fix: OnFix
    retry_checks: Callable[[], object]


class AttentionBanner(QWidget):
    """Shown while a cloud machine is left over or past its time limit; leads to the machines list."""

    def __init__(self, on_show: Callable[[], object], parent: QWidget | None = None) -> None:
        """Hidden until needed; "Show cloud machines" calls `on_show`."""
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        text = tr("A cloud machine is left over or past its time limit and may still cost money.")
        layout.addWidget(wrapped_label(text, self), 1)
        self.show_button = QPushButton(tr("Show cloud machines"), self)
        self.show_button.clicked.connect(on_show)
        layout.addWidget(self.show_button)
        self.setVisible(False)


class CloudPage(QScrollArea):
    """The Cloud page (scrolls when the window is small)."""

    def __init__(
        self,
        models: CloudModels,
        callbacks: PageCallbacks,
        clock: Clock,
        parent: QWidget | None = None,
    ) -> None:
        """Bind the sections to `models`."""
        super().__init__(parent)
        self._models = models
        self.setWidgetResizable(True)
        body = QWidget(self)
        layout = QVBoxLayout(body)
        self.check_banner = CheckBanner(callbacks.retry_checks, callbacks.fix, body)
        self._key_banner = KeyBanner(callbacks.fix, body)
        self.attention = AttentionBanner(self.show_machines, body)
        self.run_panel = RunPanel(models, clock, self.open_start, callbacks.open_job, body)
        self.start_dialog = StartDialog(models.start, callbacks.delete_unknown, callbacks.fix, self)
        self.machines = MachinesSection(models.machines, models.key, body)
        self.history = HistorySection(models.runs, body)
        self._layout = layout
        banners = (self.check_banner, self._key_banner, self.attention)
        sections = (self.run_panel, self.machines, self.history)
        for widget in (*banners, *sections):
            layout.addWidget(widget)
        layout.addStretch()
        self.setWidget(body)
        for model in (models.machines, models.poll, models.key):
            model.subscribe(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        """Show the banners: the failed check (the machines' check first), the missing key, the machines' attention."""
        models = self._models
        machines = models.machines
        error = machines.error or models.poll.shown_error()
        self.check_banner.show_error(error)
        self._key_banner.setVisible(models.key.missing)
        self.attention.setVisible(machines.attention)

    def add_section(self, section: QWidget) -> None:
        """Show another area's section below the current run."""
        section.setParent(self.widget())
        below_run = self._layout.indexOf(self.run_panel) + 1
        self._layout.insertWidget(below_run, section)

    def check_now(self) -> None:
        """Check the runs and the results now (another area added a job that may have results)."""
        self._models.poll.refresh_now()

    @override
    def showEvent(self, event: QShowEvent) -> None:
        """Ask again whether the Runpod key is set (it may have been stored in Settings meanwhile)."""
        self._models.key.check()
        super().showEvent(event)

    def open_start(self) -> None:
        """Open the start dialog (it plans at once)."""
        self._models.start.show_dialog()
        self.start_dialog.open()

    def show_machines(self) -> None:
        """Scroll to the cloud machines list."""
        self.ensureWidgetVisible(self.machines)
