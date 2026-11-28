"""The status bar line of the window: the cloud run, a machine needing attention, the busy jobs (links)."""

from collections.abc import Callable
from html import escape

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QWidget

from transcribe_ui.gui.activity import Activity
from transcribe_ui.gui.cloud.area import CloudModels
from transcribe_ui.gui.cloud.constants import COUNTDOWN_TICK_MS, LINK_SEPARATOR, STATUS_SEPARATOR
from transcribe_ui.gui.cloud.ports import Clock
from transcribe_ui.gui.cloud.status import StatusItem, status_items
from transcribe_ui.gui.constants import PROGRESS_REPAINT_MS

# Follows a status bar link: its target (`StatusTarget`) and argument (a job's batch id, or "").
type FollowLink = Callable[[str, str], object]


def _link(shown: StatusItem) -> str:
    target = escape(f"{shown.target}{LINK_SEPARATOR}{shown.argument}")
    text = escape(shown.text)
    return f'<a href="{target}">{text}</a>'


def _timer(parent: QWidget, interval_ms: int, tick: Callable[[], None]) -> QTimer:
    timer = QTimer(parent)
    timer.setInterval(interval_ms)
    timer.timeout.connect(tick)
    return timer


class StatusLine(QLabel):
    """The linked items of `status_items`, repainted on every change and each second while a run counts down."""

    def __init__(
        self,
        models: CloudModels,
        activity: Activity,
        clock: Clock,
        on_link: FollowLink,
        parent: QWidget | None = None,
    ) -> None:
        """Show the items of the models; a click on a link calls `on_link(target, argument)`."""
        super().__init__(parent)
        self._runs = models.runs
        self._activity = activity
        self._clock = clock
        self._on_link = on_link
        self._models = models
        self.shown: tuple[StatusItem, ...] = ()
        self.linkActivated.connect(self._follow)
        self.timer = _timer(self, COUNTDOWN_TICK_MS, self.refresh)
        # Progress events come often: their repaints wait at most `PROGRESS_REPAINT_MS`.
        self._throttle = _timer(self, PROGRESS_REPAINT_MS, self.refresh)
        self._throttle.setSingleShot(True)
        for model in (models.runs, models.poll, models.machines):
            model.subscribe(self.refresh)
        activity.subscribe(self._activity_changed)
        self.refresh()

    def refresh(self) -> None:
        """Show the current items."""
        self._throttle.stop()
        now = self._clock()
        self.shown = status_items(self._models, self._activity, now)
        links = [_link(shown) for shown in self.shown]
        self.setText(STATUS_SEPARATOR.join(links))
        current = self._runs.current
        finish = None if current is None else current.estimated_finish_at
        counting = finish is not None and self._runs.run_active()
        if not counting:
            self.timer.stop()
        elif not self.timer.isActive():
            self.timer.start()

    def _activity_changed(self) -> None:
        if not self._throttle.isActive():
            self._throttle.start()

    def _follow(self, link: str) -> None:
        target, _separator, argument = link.partition(LINK_SEPARATOR)
        self._on_link(target, argument)
