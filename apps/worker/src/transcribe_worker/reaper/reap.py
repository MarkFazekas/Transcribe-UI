"""One reaper pass: cancel overdue queued pods, then terminate overdue pods (§5.2, layer 4).

Only names with the app prefix are considered. A name with the prefix that does not parse is
left alone and fails the pass, so GitHub notifies; other pods are never touched or logged.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
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
    LOGGED_NAME_MAX_LENGTH,
    PASS_BUDGET,
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
    list_entries: Callable[[], list[ListedPod]]
    is_active: Callable[[ListedPod], bool]
    remove: Callable[[str], None]
    removed: Callable[[str], bool]


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

    def out_of_time(self) -> bool:
        """Return True once the pass has used up its budget."""
        return self.ports.now() - self.started > PASS_BUDGET


def reap(api: ReaperApi, ports: ReaperPorts, *, dry_run: bool) -> bool:
    """Run one pass; return True if nothing failed and no malformed app name was found.

    Queued pods go first: a queue entry that deploys while it is being cancelled then shows
    up in the pod list read afterwards.

    Args:
        api: The Runpod API.
        ports: Clock and sleep.
        dry_run: Only report what would be done; send no DELETE.
    """
    state = _Pass(ports, dry_run, ports.now())
    queued = _Kind(
        label="queued pod",
        action="cancel",
        list_entries=api.list_queued_pods,
        is_active=_waits_in_queue,
        remove=api.cancel_queued_pod,
        removed=api.queued_pod_resolved,
    )
    pods = _Kind(
        label="pod",
        action="terminate",
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


def _waits_in_queue(entry: ListedPod) -> bool:
    return entry.status == QUEUED_STATUS


def _not_terminated(entry: ListedPod) -> bool:
    return entry.status != TERMINATED_STATUS


def _reap_kind(kind: _Kind, state: _Pass) -> None:
    try:
        listed = _with_retries(kind.list_entries, state.ports, f"list {kind.label}s")
    except PodApiError as error:
        logger.error("list %ss failed: %s", kind.label, error)  # noqa: TRY400 - message only, no traceback
        state.fail()
        return
    ours = [entry for entry in listed if _is_ours(kind, entry)]
    logger.info("%d %ss listed, %d active with the app prefix", len(listed), kind.label, len(ours))
    for entry in ours:
        if state.out_of_time():
            logger.error("pass budget used up; the remaining %ss are left for the next run", kind.label)
            state.fail()
            return
        _reap_entry(kind, entry, state)


def _is_ours(kind: _Kind, entry: ListedPod) -> bool:
    return is_app_pod_name(entry.name) and kind.is_active(entry)


def _reap_entry(kind: _Kind, entry: ListedPod, state: _Pass) -> None:
    parsed = parse_pod_name(entry.name)
    if parsed is None:
        shown = entry.name[:LOGGED_NAME_MAX_LENGTH]
        logger.warning("%s=%s name=%r has the app prefix but is malformed: left alone", kind.label, entry.pod_id, shown)
        state.fail()
        return
    context = _context(kind, entry, parsed)
    if state.started <= parsed.deadline + GRACE:
        logger.info("%s action=keep outcome=within deadline and grace", context)
    elif state.dry_run:
        logger.info("%s action=%s outcome=skipped (dry run)", context, kind.action)
    elif not _remove(kind, entry.pod_id, state.ports, context):
        state.fail()


def _context(kind: _Kind, entry: ListedPod, parsed: PodName) -> str:
    deadline = parsed.deadline.isoformat()
    return f"{kind.label}={entry.pod_id} run={parsed.run_id} deadline={deadline}"


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
