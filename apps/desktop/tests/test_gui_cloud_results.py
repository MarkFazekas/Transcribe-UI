"""The Cloud area without a Runpod key, and the results poll (plan m6 M6.13; architecture §3.9 "Roles", "Results poll").

On the Cloud area's view-models over the launch world, driven by a manual executor and timer:
without a key the start-up still checks the runs and results (`refresh_status`) and leaves out
only the machines check; the poll runs while the last check says results are awaited, and stops
once it says they are not (a retrying error keeps the last answer); a change of jobs elsewhere
starts it again; Refresh now during a check checks once more after it.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from support.cloud import CloudArea, CloudWorld, ManualExecutor, cloud_area, cloud_world
from transcribe_ui.api.classes import ErrorBody, ErrorResponse
from transcribe_ui.credentials.classes import CredentialsStatusResponse, CredentialStatus, KeyringState
from transcribe_ui.gui.cloud.startup import register_startup
from transcribe_ui.gui.signals import StartupHooks
from transcribe_ui.runs.responses import RefreshStatusResponse

REFRESH = "refresh_status"
LIST = "list_runs"
CREDENTIALS = "credentials_status"
# A job id `batches_changed` names.
FIRST_JOB = "batch_20260101_000001"


@pytest.fixture
def world(tmp_path: Path) -> Iterator[CloudWorld]:
    """Return a launch leg: the published batch (awaiting its results) and another computer's job."""
    with cloud_world(tmp_path) as created:
        yield created


@pytest.fixture
def manual_area(world: CloudWorld) -> tuple[CloudArea, ManualExecutor]:
    """Return the Cloud area's view-models on a manual executor."""
    area, manual = cloud_area(world.backend, manual=True)
    assert manual is not None
    return area, manual


def _credentials(source: str, keyring: KeyringState = "available") -> CredentialsStatusResponse:
    key = CredentialStatus(name="runpod_api_key", environment_variable="RUNPOD_API_KEY", source=source)
    return CredentialsStatusResponse(keyring=keyring, credentials=(key,))


def _refreshed(*, awaiting: bool) -> RefreshStatusResponse:
    return RefreshStatusResponse(runs=(), batches=(), awaiting_results=awaiting)


def _actions(manual: ManualExecutor) -> list[str]:
    return [held.request.action for held in manual.held]


def test_no_key_still_checks_the_results(manual_area: tuple[CloudArea, ManualExecutor]) -> None:
    """A missing key leaves out only `reconcile_cloud`; `refresh_status` checks the results, and the poll follows them."""
    area, manual = manual_area
    hooks = StartupHooks()
    register_startup(hooks, area.models)
    done: list[bool] = []
    hooks.run(lambda: done.append(True))
    manual.answer(LIST)
    manual.answer(CREDENTIALS, _credentials("missing"))
    assert (area.models.key.missing, _actions(manual)) == (True, [LIST, CREDENTIALS, REFRESH])
    # The world's published batch waits for its results.
    manual.answer(REFRESH)
    assert (done, area.models.poll.awaiting, area.timers.poll.active) == ([True], True, True)


def test_a_key_also_checks_the_machines(manual_area: tuple[CloudArea, ManualExecutor]) -> None:
    """With the key, the start-up checks the cloud machines before the runs and results."""
    area, manual = manual_area
    hooks = StartupHooks()
    register_startup(hooks, area.models)
    hooks.run()
    manual.answer(LIST)
    manual.answer(CREDENTIALS, _credentials("keyring"))
    manual.answer("reconcile_cloud")
    assert (area.models.key.missing, _actions(manual)[-2:]) == (False, [LIST, REFRESH])


def test_a_failed_keyring_may_hold_the_key(manual_area: tuple[CloudArea, ManualExecutor]) -> None:
    """A keyring that failed while reading counts as holding the key: the backend's error says what is wrong."""
    key = manual_area[0].models.key
    manual = manual_area[1]
    key.check()
    manual.answer(CREDENTIALS, _credentials("missing", keyring="failed"))
    assert (key.present, key.missing) == (True, False)


def test_the_poll_runs_while_results_are_awaited(manual_area: tuple[CloudArea, ManualExecutor]) -> None:
    """Awaited results keep the poll going without a run; a retrying error keeps it; "not awaited" stops it."""
    area, manual = manual_area
    poll = area.models.poll
    timer = area.timers.poll
    poll.tick()
    manual.answer(REFRESH, _refreshed(awaiting=True))
    assert timer.active
    timer.fire()
    busy = ErrorResponse(action=REFRESH, error=ErrorBody(code="cloud_unreachable", message="Unreachable."))
    manual.answer(REFRESH, busy)
    assert (timer.active, poll.awaiting) == (True, True)
    timer.fire()
    manual.answer(REFRESH, _refreshed(awaiting=False))
    assert (timer.active, poll.awaiting) == (False, False)


def test_a_change_of_jobs_starts_the_poll_again(manual_area: tuple[CloudArea, ManualExecutor]) -> None:
    """`batches_changed` (an upload, an adoption) starts a stopped poll; its next check decides whether it goes on."""
    area, manual = manual_area
    timer = area.timers.poll
    area.models.poll.start((FIRST_JOB,))
    assert (timer.active, _actions(manual)) == (True, [])
    starts = timer.starts
    area.models.poll.start(())
    assert timer.starts == starts, "a running poll keeps its interval"
    timer.fire()
    manual.answer(REFRESH, _refreshed(awaiting=False))
    assert not timer.active


def test_refresh_now_during_a_check_checks_again(manual_area: tuple[CloudArea, ManualExecutor]) -> None:
    """Refresh now while a check is in flight (a job was just adopted) sends one more check once it answered."""
    area, manual = manual_area
    poll = area.models.poll
    poll.tick()
    poll.refresh_now()
    poll.refresh_now()
    assert _actions(manual) == [REFRESH]
    manual.answer(REFRESH, _refreshed(awaiting=False))
    assert [held.request.action for held in manual.pending(REFRESH)] == [REFRESH]
    manual.answer(REFRESH, _refreshed(awaiting=False))
    assert manual.pending(REFRESH) == []
