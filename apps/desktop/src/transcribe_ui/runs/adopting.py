"""Looking a run's pod up once after its request's answer was lost (plan m5 §1 "Create calls are never re-sent")."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final

from transcribe_ui.api.errors import CancelledError
from transcribe_ui.runpod.errors import RunpodApiError
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runs.responses import LaunchOutcome, RunInfo
from transcribe_ui.runs.settling import Listed, resolve_launching
from transcribe_ui.state.store import LocalState

# Launch outcome of a run adopted after a lost answer, by the status the adoption gave it; a
# queued pod that had already ended (cancelled, expired, failed) gives a final outcome.
ADOPTED_OUTCOMES: Final[Mapping[str, LaunchOutcome]] = MappingProxyType(
    {"running": "pod", "queued": "queued", "cancelled": "ended", "failed": "ended"},
)


@dataclass(frozen=True)
class Launched:
    """How the pod request ended, and the run as it is now."""

    outcome: LaunchOutcome
    adopted: bool
    run: RunInfo


def look_up(state: LocalState, pods: PodsApi, run_id: str, now: datetime) -> Launched:
    """Look the run's name up once after an ambiguous failure; adopt what is found.

    A failed or cancelled lookup leaves the run `launching` (`unconfirmed`): later status checks
    resolve it.
    """
    try:
        listed = Listed.read(pods)
    except (RunpodApiError, CancelledError):
        listed = Listed((), ())
    followed = resolve_launching(state, run_id, listed, now)
    outcome = ADOPTED_OUTCOMES.get(followed.status)
    if outcome is None:
        return Launched("unconfirmed", adopted=False, run=followed)
    return Launched(outcome, adopted=True, run=followed)
