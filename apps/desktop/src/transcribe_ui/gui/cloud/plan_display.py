"""Display data of the start dialog's launch plan and of the cloud machines list (architecture §3.1 "GUI").

Qt-free. The plan and the machines come from the backend's responses (`plan_launch`,
`reconcile_cloud`); only presentation is decided here: texts, numbers, money and durations,
which waits for a machine are offered within the backend's bounds, and the label of a machine's
stop (a straggler's or an overdue one's, from the backend's `role` and `overdue`).
"""

from dataclasses import dataclass

from transcribe_ui.gui.cloud.constants import WAIT_CHOICES_S
from transcribe_ui.gui.cloud.display import money
from transcribe_ui.i18n.catalog import ntr, tr
from transcribe_ui.i18n.formats import format_duration, format_number
from transcribe_ui.i18n.texts import code_text
from transcribe_ui.runs.classes import LaunchPlan, ScopeBatch, UnreadableReason, WaitBounds
from transcribe_ui.runs.responses import CloudEntry


@dataclass(frozen=True)
class PlanBatchRow:
    """A job the start would transcribe."""

    batch_id: str
    # Whether this computer knows the job (a job of another computer can be deleted from the cloud, Q28).
    known: bool
    origin: str
    files: str
    transcriptions: str
    audio: str


@dataclass(frozen=True)
class PlanView:
    """A launch plan as the start dialog shows it."""

    summary: str
    cost: str
    # The note of a fallback price, or "".
    price_note: str
    time_limit: str
    batches: tuple[PlanBatchRow, ...]
    # The objects in the cloud the machine skips (key and why).
    unreadable: tuple[str, ...]
    # Nothing is waiting for transcription: Start has nothing to do.
    empty: bool


@dataclass(frozen=True)
class MachineRow:
    """A live cloud machine (pod or queued pod) of the app."""

    entry: CloudEntry
    kind: str
    name: str
    status: str
    run_id: str
    role: str
    # "Terminate" (pod) or "Cancel" (queued pod) for a straggler or an overdue one; None for the current run's.
    action_label: str | None


def _plan_batch(batch: ScopeBatch) -> PlanBatchRow:
    return PlanBatchRow(
        batch_id=batch.batch_id,
        known=batch.known,
        origin=tr("This computer") if batch.known else tr("Another computer"),
        files=format_number(batch.files),
        transcriptions=format_number(len(batch.pending_jobs)),
        audio=format_duration(batch.pending_duration_s),
    )


def _unreadable_text(key: str, reason: UnreadableReason) -> str:
    reasons: dict[UnreadableReason, str] = {
        "not_a_manifest": tr("not a job description"),
        "unsupported_schema": tr("written by another version of the app"),
        "invalid": tr("damaged"),
    }
    return f"{key}: {reasons[reason]}"


def plan_view(plan: LaunchPlan) -> PlanView:
    """Return a launch plan as the start dialog shows it."""
    scope = plan.scope
    cost = plan.cost
    summary = ntr(
        "{jobs} transcription in {batches} job, {audio} of audio.",
        "{jobs} transcriptions in {batches} jobs, {audio} of audio.",
        scope.pending_jobs,
    )
    named = {
        "jobs": format_number(scope.pending_jobs),
        "batches": format_number(len(scope.batches)),
        "audio": format_duration(scope.pending_duration_s),
    }
    price = tr("Estimated cost: {low} to {high} ({price} per hour).").format(
        low=money(cost.low_usd),
        high=money(cost.high_usd),
        price=money(cost.price_per_hour_usd),
    )
    limit = tr("The cloud machine stops itself after at most {duration}.").format(
        duration=format_duration(plan.max_runtime_s),
    )
    note = "" if cost.price_source == "catalog" else code_text("price_source", cost.price_source)
    return PlanView(
        summary=summary.format_map(named),
        cost=price,
        price_note=note,
        time_limit=limit,
        batches=tuple(_plan_batch(batch) for batch in scope.batches),
        unreadable=tuple(_unreadable_text(found.key, found.reason) for found in scope.unreadable),
        empty=not scope.pending_jobs,
    )


def wait_choices(bounds: WaitBounds) -> tuple[tuple[int, str], ...]:
    """Return the waits for a machine to offer (seconds, label): the usual ones within the bounds, and the default."""
    offered = {choice for choice in WAIT_CHOICES_S if bounds.min_s <= choice <= bounds.max_s}
    offered.add(bounds.default_s)
    return tuple((seconds, format_duration(seconds)) for seconds in sorted(offered))


def machine_row(entry: CloudEntry) -> MachineRow:
    """Return a live cloud machine as the machines list shows it; a straggler or an overdue one gets its stop."""
    pod = entry.kind == "pod"
    needs_stop = entry.role == "straggler" or entry.overdue
    role = tr("Current run") if entry.role == "current" else tr("Left over")
    if entry.overdue:
        role = tr("{role}, past its time limit").format(role=role)
    action = None
    if needs_stop:
        action = tr("Terminate") if pod else tr("Cancel")
    return MachineRow(
        entry=entry,
        kind=tr("Machine") if pod else tr("Waiting for a machine"),
        name=entry.name,
        status=entry.status,
        run_id=entry.run_id or "",
        role=role,
        action_label=action,
    )
