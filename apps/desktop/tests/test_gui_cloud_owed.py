"""A change of jobs owes the poll a check (plan m6 M6.13; architecture §3.1 "Cloud").

On the Cloud area's view-models over the launch world, driven by a manual executor and timer:
after `batches_changed` only a check's final answer stops the poll. A lasting error settles the
change (the next tick would only repeat it); a change made while a check is in flight is not
answered by that check.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from support.cloud import CloudArea, ManualExecutor, cloud_area, cloud_world
from transcribe_ui.api.classes import ErrorBody, ErrorResponse
from transcribe_ui.runs.responses import RefreshStatusResponse

REFRESH = "refresh_status"
# A job id `batches_changed` names.
FIRST_JOB = "batch_20260101_000001"


@pytest.fixture
def area(tmp_path: Path) -> Iterator[tuple[CloudArea, ManualExecutor]]:
    """Return the Cloud area's view-models over a launch leg, on a manual executor."""
    with cloud_world(tmp_path) as world:
        created, manual = cloud_area(world.backend, manual=True)
        assert manual is not None
        yield created, manual


def _nothing_awaited() -> RefreshStatusResponse:
    return RefreshStatusResponse(runs=(), batches=(), awaiting_results=False)


def test_a_lasting_error_settles_a_change(area: tuple[CloudArea, ManualExecutor]) -> None:
    """An error the next tick would only repeat (`r2_auth_failed`) answers for the change: the poll stops."""
    cloud, manual = area
    cloud.models.poll.start((FIRST_JOB,))
    cloud.timers.poll.fire()
    lasting = ErrorResponse(action=REFRESH, error=ErrorBody(code="r2_auth_failed", message="Refused."))
    manual.answer(REFRESH, lasting)
    assert not cloud.timers.poll.active


def test_a_retrying_error_keeps_the_change_owed(area: tuple[CloudArea, ManualExecutor]) -> None:
    """A retrying error (`cloud_unreachable`) settles nothing: the poll goes on."""
    cloud, manual = area
    cloud.models.poll.start((FIRST_JOB,))
    cloud.timers.poll.fire()
    busy = ErrorResponse(action=REFRESH, error=ErrorBody(code="cloud_unreachable", message="Unreachable."))
    manual.answer(REFRESH, busy)
    assert cloud.timers.poll.active


def test_a_change_during_a_check_is_still_owed(area: tuple[CloudArea, ManualExecutor]) -> None:
    """A change made after the check in flight was sent is not answered by it: the poll goes on to the next check."""
    cloud, manual = area
    cloud.models.poll.tick()
    cloud.models.poll.start((FIRST_JOB,))
    manual.answer(REFRESH, _nothing_awaited())
    during = cloud.timers.poll.active
    cloud.timers.poll.fire()
    manual.answer(REFRESH, _nothing_awaited())
    assert (during, cloud.timers.poll.active) == (True, False)


def test_the_note_names_finished_cleanups(area: tuple[CloudArea, ManualExecutor]) -> None:
    """A check that finished an interrupted cleanup says so in the page's note until the next check."""
    cloud, manual = area
    poll = cloud.models.poll
    poll.tick()
    done = RefreshStatusResponse(runs=(), batches=(), awaiting_results=False, finished_cleanups=(FIRST_JOB,))
    manual.answer(REFRESH, done)
    noted = poll.note()
    poll.tick()
    manual.answer(REFRESH, _nothing_awaited())
    assert (noted, poll.note()) == ("Finished an interrupted cloud cleanup of another computer's job.", "")
