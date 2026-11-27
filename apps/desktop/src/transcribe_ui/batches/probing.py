"""Probing the items of the review step (architecture §3.3 "Review step").

Every item in phase `reviewing` is probed: its file is stat-ed, the probe cache (path + size +
mtime) is asked first and ffprobe runs only on a miss, outside any database transaction. An
unreadable cache row (an older payload shape) counts as a miss and is overwritten. The file is
stat-ed again after ffprobe: a file that changed meanwhile is probed once more, and if it
changed again it is an item problem (`source_changing`) and nothing is cached. The outcome is
stored per item in its own transaction, so a cancelled or interrupted review keeps the items
probed so far. Each job gets its pre-selected choice (`batches/choices.py`), unless
the file probes the same as before (choices made earlier stay). A missing file or a failed probe
is an item problem, not a failure of the request: the item stays in `reviewing` until it probes
well or is excluded.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from transcribe_ui.api.classes import ProgressEvent
from transcribe_ui.api.errors import DomainError
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.choices import preselected_choice
from transcribe_ui.batches.constants import (
    PROBE_ATTEMPTS,
    PROBE_FAILURE_CODES,
    PROBE_STAGE,
    PROBLEM_PROBE_FAILED,
    PROBLEM_SOURCE_CHANGING,
    PROBLEM_SOURCE_MISSING,
    REVIEW_PHASES,
    SOURCE_CHANGING_MESSAGE,
    SOURCE_MISSING_MESSAGE,
)
from transcribe_ui.batches.payloads import ItemPayload, ItemProblem
from transcribe_ui.batches.phases import load_batch, require_phase, set_review_phase, touch_batch
from transcribe_ui.batches.probe_cache import FileStat, cached_probe, file_stat, remember_probe
from transcribe_ui.batches.records import StoredItem, stored_items
from transcribe_ui.media.classes import ProbeResult
from transcribe_ui.state.models import ItemRow, JobRow


@dataclass(frozen=True)
class _Probed:
    """The outcome of probing one item: a result or a problem, with the file's size and mtime."""

    stat: FileStat | None
    probe: ProbeResult | None
    problem: ItemProblem | None
    # Whether the result came from the probe cache (so there is nothing to remember).
    cached: bool = False


# The outcome of a file that kept changing while it was probed.
_CHANGING = _Probed(None, None, ItemProblem(code=PROBLEM_SOURCE_CHANGING, message=SOURCE_CHANGING_MESSAGE))
# The outcome of a file that is gone or cannot be read.
_MISSING = _Probed(None, None, ItemProblem(code=PROBLEM_SOURCE_MISSING, message=SOURCE_MISSING_MESSAGE))


def _cached(context: RequestContext, path: str, stat: FileStat) -> ProbeResult | None:
    with context.ports.state.session() as session:
        return cached_probe(session, path, stat)


def _probe_once(context: RequestContext, path: str, stat: FileStat) -> _Probed | None:
    """Probe the file; return None when it changed during the probe (nothing is trusted then)."""
    try:
        probed = context.ports.media.probe(Path(path), context.cancel)
    except DomainError as error:
        if error.code not in PROBE_FAILURE_CODES:
            raise
        outcome = _Probed(stat, None, ItemProblem(code=PROBLEM_PROBE_FAILED, message=error.message))
    else:
        outcome = _Probed(stat, probed, None)
    return outcome if file_stat(path) == stat else None


def _probe(context: RequestContext, path: str) -> _Probed:
    """Probe the file, from the cache when it has it; a file that changes during every attempt is `source_changing`."""
    for _ in range(PROBE_ATTEMPTS):
        stat = file_stat(path)
        if stat is None:
            return _MISSING
        cached = _cached(context, path, stat)
        if cached is not None:
            return _Probed(stat, cached, None, cached=True)
        outcome = _probe_once(context, path, stat)
        if outcome is not None:
            return outcome
    return _CHANGING


def _remember(session: Session, path: str, probed: _Probed, now: datetime) -> None:
    if probed.stat is not None and probed.probe is not None and not probed.cached:
        remember_probe(session, path, probed.stat, probed.probe, now)


def _probed_payload(previous: ItemPayload, probed: _Probed) -> ItemPayload:
    changes: dict[str, object] = {"probe": probed.probe, "problem": probed.problem}
    if probed.stat is not None:
        changes.update(size_bytes=probed.stat.size_bytes, mtime_ns=probed.stat.mtime_ns)
    return previous.model_copy(update=changes)


def _apply_probe(session: Session, member: StoredItem, probed: _Probed) -> None:
    """Store the probe outcome on the item, pre-select its jobs unless the file is unchanged, set its phase."""
    payload = _probed_payload(member.payload, probed)
    keep_choices = probed.probe is not None and probed.probe == member.payload.probe
    row = session.get(ItemRow, member.item_id)
    assert row is not None, "the item is locked by its batch lock"
    for job in member.jobs:
        job_row = session.get(JobRow, job.row.job_id)
        assert job_row is not None, "the job is locked by its batch lock"
        if probed.probe is not None and not keep_choices:
            kind, job_payload = preselected_choice(payload, probed.probe, job_row.language)
            job_row.kind = kind
            job_row.payload = job_payload.model_dump(mode="json")
    row.payload = payload.model_dump(mode="json")
    session.flush()


def _items_to_probe(context: RequestContext, batch_id: str) -> list[StoredItem]:
    with context.ports.state.session() as session:
        require_phase(load_batch(session, batch_id), REVIEW_PHASES)
        return [member for member in stored_items(session, batch_id) if member.phase == "reviewing"]


def _progress(action: str, done: int, total: int, path: str) -> ProgressEvent:
    return ProgressEvent(action=action, stage=PROBE_STAGE, completed=done, total=total, path=path)


def probe_waiting_items(context: RequestContext, batch_id: str, action: str) -> None:
    """Probe every item of the batch in phase `reviewing`, one transaction per item, with progress.

    Raises:
        WrongPhaseError: If the batch is not in `reviewing` or `uploading`.
        CancelledError: If cancelled between two items (the items done so far are kept).
    """
    pending = _items_to_probe(context, batch_id)
    state = context.ports.state
    total = len(pending)
    for done, member in enumerate(pending, start=1):
        context.check_cancelled()
        path = member.payload.path
        probed = _probe(context, path)
        with state.session() as session:
            row = load_batch(session, batch_id)
            require_phase(row, REVIEW_PHASES)
            now = context.ports.clock.now()
            _remember(session, path, probed, now)
            _apply_probe(session, member, probed)
            set_review_phase(session, member.item_id)
            touch_batch(session, batch_id, (row.phase,), now=now)
        context.progress.emit(_progress(action, done, total, path))
