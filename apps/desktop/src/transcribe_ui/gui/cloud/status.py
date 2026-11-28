"""The window's status bar line: the cloud run and the jobs with a long step running (plan m6 §1 "Layout").

Qt-free. Each item links back to where it is shown: the run (its stage and the remaining time,
counted down) to the Cloud page, a cloud machine that needs attention to the cloud machines list,
a failed check of the cloud to the Cloud page, and each job whose long step (converting,
uploading, downloading, cleaning up) is queued or running in the activity panel to its job.
"""

from dataclasses import dataclass
from datetime import datetime

from transcribe_ui.gui.activity import Activity
from transcribe_ui.gui.cloud.constants import StatusTarget
from transcribe_ui.gui.cloud.display import countdown_text, is_final
from transcribe_ui.gui.cloud.machines import MachinesModel
from transcribe_ui.gui.cloud.runs import RunList
from transcribe_ui.i18n.catalog import tr
from transcribe_ui.i18n.texts import code_text


@dataclass(frozen=True)
class StatusItem:
    """One linked item of the status bar line."""

    text: str
    target: StatusTarget
    # The job of a `job` item; "" otherwise.
    argument: str = ""


def _run_item(runs: RunList, now: datetime) -> StatusItem | None:
    current = runs.current
    if current is None or is_final(current):
        return None
    stage = code_text("run_stage", current.stage)
    remaining = countdown_text(current.estimated_finish_at, now)
    text = tr("Cloud: {stage}").format(stage=stage)
    if remaining:
        text = tr("Cloud: {stage}, {remaining}").format(stage=stage, remaining=remaining)
    return StatusItem(text, "cloud")


def _job_items(activity: Activity) -> list[StatusItem]:
    found = []
    for entry in activity.entries:
        batch_id = getattr(entry.request, "batch_id", None)
        if entry.can_cancel and isinstance(batch_id, str):
            text = tr("Job {batch_id}: {status}").format(batch_id=batch_id, status=entry.status)
            found.append(StatusItem(text, "job", batch_id))
    return found


def status_items(runs: RunList, machines: MachinesModel, activity: Activity, now: datetime) -> tuple[StatusItem, ...]:
    """Return the status bar's items: the run, the cloud machines' attention or check error, the busy jobs."""
    found: list[StatusItem] = []
    run = _run_item(runs, now)
    if run is not None:
        found.append(run)
    if machines.attention:
        found.append(StatusItem(tr("A cloud machine needs attention"), "machines"))
    if machines.error is not None:
        found.append(StatusItem(tr("The cloud could not be checked"), "cloud"))
    found.extend(_job_items(activity))
    return tuple(found)
