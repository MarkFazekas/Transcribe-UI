"""Whether a straggler still carries a final run's name in Runpod (collection waits for it).

A final run's own pod and queued pod were verified gone (or never existed), so an entry with the
run's name and another id is a straggler that may still write `runs/<run_id>/`. It counts while
it is live by the run-activity check's rules (`runs/pods.py`): a pod in any status but
`TERMINATED`, or a queued pod `queued_pod_active` keeps (a `DEPLOYED` one is judged by its pod,
looked up by id when the list does not show it, until its name deadline).

The run's own recorded ids are left out: the lists were read before this refresh may have
stopped them. Collection is housekeeping: with no open run, lists that cannot be read only put
it off (logged), and a failed lookup counts as a straggler for that run; only open runs make a
Runpod failure fail `refresh_status`.
"""

import logging
from datetime import datetime
from functools import partial

from transcribe_ui.runpod.access import api_failure
from transcribe_ui.runpod.constants import POD_TERMINATED
from transcribe_ui.runpod.errors import RunpodApiError
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runs.collect import StragglerCheck
from transcribe_ui.runs.errors import runpod_call
from transcribe_ui.runs.payloads import RunPayload
from transcribe_ui.runs.pods import queued_pod_active
from transcribe_ui.runs.records import has_uncollected, open_runs
from transcribe_ui.runs.settling import Listed
from transcribe_ui.state.store import LocalState

logger = logging.getLogger(__name__)


def straggler_listed(name: str, payload: RunPayload, listed: Listed, pods: PodsApi, now: datetime) -> bool:
    """Return whether a live pod or queued pod other than the run's own carries its name.

    Raises:
        RunpodApiError: The lookup of a deployed queued pod's pod failed.
    """
    named = [pod for pod in listed.pods if pod.name == name]
    others = [pod for pod in named if pod.pod_id != payload.pod_id]
    if any(pod.status != POD_TERMINATED for pod in others):
        return True
    by_id = {pod.pod_id: pod for pod in listed.pods}
    queued = [entry for entry in listed.queued if entry.name == name]
    other_entries = [entry for entry in queued if entry.queued_pod_id != payload.queued_pod_id]
    return any(queued_pod_active(entry, by_id, pods, now) for entry in other_entries)


def read_lists(state: LocalState, pods: PodsApi) -> Listed | None:
    """Read the Runpod lists once, when a run is open or a final run's objects wait for collection.

    None means: no lists, so nothing is collected this time. Collection is housekeeping: with no
    open run, a failed read is logged and the refresh goes on without it.

    Raises:
        DomainError: The lists could not be read while a run is open (nothing was changed).
    """
    if open_runs(state):
        return runpod_call(lambda: Listed.read(pods))
    if not has_uncollected(state):
        return None
    try:
        return Listed.read(pods)
    except RunpodApiError as error:
        failure = api_failure(error).code
    logger.warning("The Runpod lists could not be read (%s); run objects are collected later", failure)
    return None


def straggler_check(listed: Listed, pods: PodsApi, now: datetime) -> StragglerCheck:
    """Return the check whether a live straggler carries a final run's name."""
    return partial(_straggler_listed, listed, pods, now)


def _straggler_listed(listed: Listed, pods: PodsApi, now: datetime, name: str, payload: RunPayload) -> bool:
    """Run the check; a failed lookup counts as a straggler (logged), so that run is collected later."""
    try:
        return straggler_listed(name, payload, listed, pods, now)
    except RunpodApiError as error:
        failure = api_failure(error).code
    logger.warning("A straggler lookup failed (%s); the run's objects are collected later", failure)
    return True
