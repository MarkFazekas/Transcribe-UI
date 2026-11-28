"""Puts the Cloud area into the window: the page, the status bar line, the bus and the start-up checks.

Start-up (plan m6 §1 "Start-up sequence", `gui/cloud/startup.py`): the run list, then whether a
Runpod key is set and, with one, `reconcile_cloud` and `refresh_status`. The close question
learns from the run list whether a run is not final.
"""

from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING

from transcribe_ui.gui.cloud.area import build_models
from transcribe_ui.gui.cloud.constants import DELETE_UNKNOWN_SIGNAL
from transcribe_ui.gui.cloud.outlet import CloudWindowOutlet
from transcribe_ui.gui.cloud.ports import Clock, TimerFactory
from transcribe_ui.gui.cloud.startup import RunpodKey, register_startup
from transcribe_ui.gui.cloud.status_view import StatusLine
from transcribe_ui.gui.cloud.timers import QtTimer
from transcribe_ui.gui.cloud.view import CloudPage, PageCallbacks
from transcribe_ui.gui.constants import PAGE_CLOUD
from transcribe_ui.gui.texts import page_title

if TYPE_CHECKING:
    from transcribe_ui.gui.cloud.area import CloudModels
    from transcribe_ui.gui.shell import MainWindow


def utc_now() -> datetime:
    """Return the current moment in UTC (the countdown's clock)."""
    return datetime.now(UTC)


def _retry_checks(models: "CloudModels") -> None:
    """Retry of a failed check: the cloud machines, then the runs."""
    models.machines.check()
    models.poll.refresh_now()


def install_cloud(window: "MainWindow", timers: TimerFactory | None = None, clock: Clock = utc_now) -> CloudPage:
    """Register the Cloud page and its status bar line, connect the bus, add the start-up checks."""
    context = window.context
    outlet = CloudWindowOutlet(window)
    make_timer = partial(QtTimer, parent=window) if timers is None else timers
    models = build_models(context.executor, outlet, make_timer)
    callbacks = PageCallbacks(
        open_job=context.signals.open_job.emit,
        delete_unknown=partial(outlet.emit, DELETE_UNKNOWN_SIGNAL),
        fix=outlet.fix,
        retry_checks=partial(_retry_checks, models),
    )
    page = CloudPage(models, callbacks, clock, window)
    window.pages.add_page(PAGE_CLOUD, page_title(PAGE_CLOUD), page, needs_database=True)
    line = StatusLine(models, context.activity, clock, outlet.follow_link, window)
    window.statusBar().addPermanentWidget(line, 1)
    signals = context.signals
    signals.open_cloud_machines.connect(page.show_machines)
    signals.plan_invalidated.connect(models.start.plan_again)
    signals.runs_changed.connect(models.runs.on_runs_changed)
    register_startup(context.hooks, models, RunpodKey(context.executor))
    context.run_active = models.runs.run_active
    return page
