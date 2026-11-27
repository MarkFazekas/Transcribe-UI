"""One reaper pass: cancel overdue queued pods, then terminate overdue pods (§5.2, layer 4).

Only string names with the app prefix are considered; other pods are never touched,
validated or logged. An app entry with an invalid id or a name that does not parse, or whose deadline is
not yet due but lies implausibly far after its creation, is left alone and fails the pass, so
GitHub notifies. A missing or invalid `createdAt` also fails the pass, but the entry is still
handled, with the pass start as its creation time.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial

from transcribe_contracts.classes import PodName
from transcribe_contracts.pods import is_app_pod_name, parse_pod_name
from transcribe_worker.entry.constants import TERMINATED_STATUS
from transcribe_worker.entry.pod_api import PodApiError
from transcribe_worker.reaper.api import ReaperApi
from transcribe_worker.reaper.classes import ListedPod
from transcribe_worker.reaper.constants import (
    CALL_ATTEMPTS,
    CONFIRM_CHECKS,
    CONFIRM_DELAY_S,
    GRACE,
    ITEM_ID_PATTERN,
    LOGGED_VALUE_MAX_LENGTH,
    MAX_DEADLINE_SPAN,
    PASS_BUDGET,
    POD_PHASE_MIN_WINDOW,
    QUEUED_PHASE_BUDGET,
    QUEUED_STATUS,
    RETRY_INITIAL_DELAY_S,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReaperPorts:
    """Clock and sleep of a pass (injected by tests)."""

    now: Callable[[], datetime]
    sleep: Callable[[float], None]


@dataclass(frozen=True)
class _Kind:
    """How one kind of item (pod or queued pod) is listed, judged and removed."""

    label: str
    action: str
    budget: timedelta
    min_window: timedelta
    list_entries: Callable[[], list[ListedPod]]
    is_active: Callable[[str], bool]
    remove: Callable[[str], None]
    removed: Callable[[str], bool]


@dataclass(frozen=True)
class _AppEntry:
    """A listed entry with a string name carrying the app prefix, active or of unknown status."""

    pod_id: str | None
    name: str
    status_known: bool
    created_at: str | None


@dataclass
class _Pass:
    """State of one pass."""

    ports: ReaperPorts
    dry_run: bool
    started: datetime
    ok: bool = True

    def fail(self) -> None:
        """Mark the pass as failed (it continues with the other items)."""
        self.ok = False

    def out_of_time(self, deadline: datetime) -> bool:
        """Return True once the given moment has passed."""
        return self.ports.now() > deadline


def reap(api: ReaperApi, ports: ReaperPorts, *, dry_run: bool) -> bool:
    """Run one pass; return True if nothing failed and no malformed app name was found.

    Queued pods go first: a queue entry that deploys while it is being cancelled then shows
    up in the pod list read afterwards. The queued phase starts no new action after
    `QUEUED_PHASE_BUDGET` from the pass start (its list included); the pod phase always runs and keeps starting actions until
    `PASS_BUDGET`, or for `POD_PHASE_MIN_WINDOW` from its own start if that is later, so a slow
    queued phase cannot starve it.

    Args:
        api: The Runpod API.
        ports: Clock and sleep.
        dry_run: Only report what would be done; send no DELETE.
    """
    state = _Pass(ports, dry_run, ports.now())
    queued = _Kind(
        label="queued pod",
        action="cancel",
        budget=QUEUED_PHASE_BUDGET,
        min_window=timedelta(0),
        list_entries=api.list_queued_pods,
        is_active=_waits_in_queue,
        remove=api.cancel_queued_pod,
        removed=api.queued_pod_resolved,
    )
    pods = _Kind(
        label="pod",
        action="terminate",
        budget=PASS_BUDGET,
        min_window=POD_PHASE_MIN_WINDOW,
        list_entries=api.list_pods,
        is_active=_not_terminated,
        remove=api.terminate_pod,
        removed=api.pod_gone,
    )
    for kind in (queued, pods):
        _reap_kind(kind, state)
    suffix = " (dry run)" if dry_run else ""
    logger.info("pass %s%s", "ok" if state.ok else "FAILED", suffix)
    return state.ok


def _waits_in_queue(status: str) -> bool:
    return status == QUEUED_STATUS


def _not_terminated(status: str) -> bool:
    return status != TERMINATED_STATUS


def _reap_kind(kind: _Kind, state: _Pass) -> None:
    phase_start = state.ports.now()
    stop_at = max(state.started + kind.budget, phase_start + kind.min_window)
    try:
        listed = _with_retries(kind.list_entries, state.ports, f"list {kind.label}s")
    except PodApiError as error:
        logger.error("list %ss failed: %s", kind.label, error)  # noqa: TRY400 - message only, no traceback
        state.fail()
        return
    candidates = (_app_entry(kind, entry) for entry in listed)
    ours = [entry for entry in candidates if entry is not None]
    logger.info("%d %ss listed, %d active with the app prefix", len(listed), kind.label, len(ours))
    for entry in ours:
        if state.out_of_time(stop_at):
            logger.error("time budget used up; the remaining %ss are left for the next run", kind.label)
            state.fail()
            return
        _reap_entry(kind, entry, state)


def _app_entry(kind: _Kind, entry: ListedPod) -> _AppEntry | None:
    """Return the entry if it is an app entry not known to be inactive; None otherwise.

    Only a string name with the app prefix makes an entry ours; anything else is foreign and
    never logged. An app entry whose status is not a string is kept (it fails the pass later).
    """
    name, status = entry.name, entry.status
    if name is None or not is_app_pod_name(name):
        return None
    if status is not None and not kind.is_active(status):
        return None
    status_known = status is not None
    return _AppEntry(pod_id=entry.pod_id, name=name, status_known=status_known, created_at=entry.created_at)


def _reap_entry(kind: _Kind, entry: _AppEntry, state: _Pass) -> None:
    # Untrusted values are logged truncated and escaped (%r), so they cannot start a log line.
    pod_id = entry.pod_id
    if pod_id is None or re.fullmatch(ITEM_ID_PATTERN, pod_id) is None:
        shown = None if pod_id is None else pod_id[:LOGGED_VALUE_MAX_LENGTH]
        logger.warning("%s id=%r has the app prefix but an invalid id: left alone", kind.label, shown)
        state.fail()
        return
    parsed = parse_pod_name(entry.name)
    if parsed is None:
        shown = entry.name[:LOGGED_VALUE_MAX_LENGTH]
        logger.warning("%s=%s name=%r has the app prefix but is malformed: left alone", kind.label, pod_id, shown)
        state.fail()
        return
    context = _context(kind, pod_id, parsed)
    if not entry.status_known:
        logger.warning("%s status missing or not a string: handled by its deadline", context)
        state.fail()
    created = _created_at(entry, state, context)
    if parsed.deadline >= state.started - GRACE:  # not `deadline + GRACE`: that may overflow
        _keep(parsed, created, state, context)
    elif state.dry_run:
        logger.info("%s action=%s outcome=skipped (dry run)", context, kind.action)
    elif not _remove(kind, pod_id, state.ports, context):
        state.fail()


def _created_at(entry: _AppEntry, state: _Pass, context: str) -> datetime:
    """Return the entry's creation time, capped at the pass start (clock skew only loosens)."""
    created = _parse_aware(entry.created_at)
    if created is None:
        logger.warning("%s createdAt missing or not a date-time with offset: pass start used instead", context)
        state.fail()
        return state.started
    return min(created, state.started)


def _parse_aware(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return None if parsed.tzinfo is None else parsed


def _keep(parsed: PodName, created: datetime, state: _Pass, context: str) -> None:
    # Only a deadline still ahead can disable this layer, so only it is checked for
    # plausibility. A difference of two moments cannot overflow.
    if parsed.deadline - created <= MAX_DEADLINE_SPAN:
        logger.info("%s action=keep outcome=within deadline and grace", context)
    else:
        logger.warning("%s action=keep outcome=deadline implausibly far after creation: left alone", context)
        state.fail()


def _context(kind: _Kind, pod_id: str, parsed: PodName) -> str:
    deadline = parsed.deadline.isoformat()
    return f"{kind.label}={pod_id} run={parsed.run_id} deadline={deadline}"


def _remove(kind: _Kind, entry_id: str, ports: ReaperPorts, context: str) -> bool:
    try:
        label = f"{kind.action} {kind.label}"
        _with_retries(partial(kind.remove, entry_id), ports, label)
    except PodApiError as error:
        logger.error("%s action=%s outcome=FAILED: %s", context, kind.action, error)  # noqa: TRY400 - message only
        return False
    if _confirmed(kind, entry_id, ports):
        logger.info("%s action=%s outcome=done", context, kind.action)
        return True
    logger.error("%s action=%s outcome=FAILED: not confirmed after %d checks", context, kind.action, CONFIRM_CHECKS)
    return False


def _with_retries[Outcome](call: Callable[[], Outcome], ports: ReaperPorts, label: str) -> Outcome:
    delay = RETRY_INITIAL_DELAY_S
    for attempt in range(1, CALL_ATTEMPTS):
        outcome = _attempt(call)
        if not isinstance(outcome, PodApiError):
            return outcome
        logger.warning("%s: attempt %d of %d failed: %s", label, attempt, CALL_ATTEMPTS, outcome)
        ports.sleep(delay)
        delay *= 2
    return call()


def _attempt[Outcome](call: Callable[[], Outcome]) -> Outcome | PodApiError:
    try:
        return call()
    except PodApiError as error:
        return error


def _confirmed(kind: _Kind, entry_id: str, ports: ReaperPorts) -> bool:
    check_removed = partial(kind.removed, entry_id)
    for check in range(CONFIRM_CHECKS):
        if check:
            ports.sleep(CONFIRM_DELAY_S)
        outcome = _attempt(check_removed)
        if isinstance(outcome, PodApiError):
            logger.warning("%s=%s: confirmation check failed: %s", kind.label, entry_id, outcome)
        elif outcome:
            return True
    return False
