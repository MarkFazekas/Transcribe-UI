"""The retry and cleanup handlers together, over one activity panel and one outlet (Qt-free)."""

from dataclasses import dataclass

from transcribe_ui.gui.activity import Activity
from transcribe_ui.gui.cleanup.panel import CleanupOutlet, JobSteps
from transcribe_ui.gui.cleanup.retry import RetryModel
from transcribe_ui.gui.cleanup.viewmodel import CleanupModel


@dataclass(frozen=True)
class CleanupHandlers:
    """The handlers of `retry_jobs`, `clean_up` and `delete_unknown_batch`, and where the user is."""

    steps: JobSteps
    retry: RetryModel
    cleanup: CleanupModel


def build_handlers(activity: Activity, outlet: CleanupOutlet) -> CleanupHandlers:
    """Return the handlers, running their steps in `activity` and telling the window through `outlet`."""
    steps = JobSteps(activity, outlet)
    return CleanupHandlers(steps, RetryModel(steps), CleanupModel(steps))
