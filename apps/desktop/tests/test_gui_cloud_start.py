"""The start dialog's and the machines list's answers in every order (plan m6 M6.6; manual executor).

The executor answers only when the test says so: a dialog closed or reopened before its plan or
its launch answered, Retry after a failed plan or Start, and a check asked for during a check.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from support.cloud import CloudArea, CloudWorld, ManualExecutor, cloud_area, cloud_world
from support.launch import write_foreign
from transcribe_ui.api.classes import ErrorBody, ErrorResponse

PLAN = "plan_launch"
LAUNCH = "launch_worker"
LIST = "list_runs"
# Another computer's second job, written while a Start is in flight.
LATE_BATCH_ID = "batch_20260102_090000"


@pytest.fixture
def world(tmp_path: Path) -> Iterator[CloudWorld]:
    """Return a launch leg: the cloud set up, the published batch and another computer's job waiting."""
    with cloud_world(tmp_path) as created:
        yield created


def _manual(world: CloudWorld) -> tuple[CloudArea, ManualExecutor]:
    created, manual = cloud_area(world.backend, manual=True)
    assert manual is not None
    return created, manual


def _start(area: CloudArea, manual: ManualExecutor) -> None:
    """Open the dialog, let it plan, press Start (the launch is held)."""
    start = area.models.start
    start.show_dialog()
    manual.answer(PLAN)
    start.start()


def _down(action: str) -> ErrorResponse:
    return ErrorResponse(action=action, error=ErrorBody(code="cloud_unreachable", message="Down."))


def test_answers_after_the_dialog_closed(world: CloudWorld) -> None:
    """A plan answered after Close shows nothing; a launch answered after Close still reaches the runs."""
    area, manual = _manual(world)
    start = area.models.start
    start.show_dialog()
    start.close()
    manual.answer(PLAN)
    assert (start.phase, start.form.view) == ("closed", None)
    _close_while_starting(area, manual)


def _close_while_starting(area: CloudArea, manual: ManualExecutor) -> None:
    start = area.models.start
    _start(area, manual)
    start.close()
    manual.answer(LAUNCH)
    assert (start.phase, area.timers.poll.active, len(manual.pending(LIST))) == ("launched", True, 1)
    manual.answer_all()
    assert area.models.runs.run_active()


def test_a_changed_plan_after_close_reopens_the_dialog(world: CloudWorld) -> None:
    """Closed while starting, a Start answered with a changed plan reopens the dialog with that plan."""
    area, manual = _manual(world)
    start = area.models.start
    _start(area, manual)
    start.close()
    write_foreign(world.store, LATE_BATCH_ID)
    manual.answer(LAUNCH)
    view = start.form.view
    assert view is not None
    assert (start.phase, start.form.plan_changed, len(view.batches), start.can_start) == ("ready", True, 3, True)


def test_a_reopened_dialog_waits_for_the_earlier_start(world: CloudWorld) -> None:
    """While an earlier Start is in flight, a reopened dialog cannot start; its answer leaves a note, not a close."""
    area, manual = _manual(world)
    start = area.models.start
    _start(area, manual)
    start.close()
    start.show_dialog()
    manual.answer(PLAN)
    assert (start.phase, start.can_start, start.notice) == ("ready", False, "Your earlier Start is still being sent.")
    manual.answer(LAUNCH)
    note = "The cloud machine of your earlier Start is starting; the Cloud page shows it."
    assert (start.phase, start.notice, area.timers.poll.active) == ("planning", note, True)
    assert len(manual.pending(PLAN)) == 1, "the plan shown before that run is planned again"
    manual.answer_all()
    assert (start.phase, start.can_start) == ("ready", False), "Start waits while that run is active"


def test_retry_after_a_failed_plan_plans_again(world: CloudWorld) -> None:
    """Retry after a failed plan plans again, even while an earlier plan is shown."""
    area, manual = _manual(world)
    start = area.models.start
    start.show_dialog()
    manual.answer(PLAN)
    start.plan_again()
    manual.answer(PLAN, _down(PLAN))
    start.retry()
    assert (len(manual.pending(PLAN)), manual.pending(LAUNCH)) == (1, [])


def test_retry_after_a_failed_start_starts_again(world: CloudWorld) -> None:
    """Retry after a failed Start sends the launch again."""
    area, manual = _manual(world)
    start = area.models.start
    _start(area, manual)
    manual.answer(LAUNCH, _down(LAUNCH))
    start.retry()
    assert len(manual.pending(LAUNCH)) == 1


def test_a_check_asked_during_a_check_is_sent_after_it(world: CloudWorld) -> None:
    """A check asked for while one is in flight is sent once that one answers; both callers proceed after it."""
    area, manual = _manual(world)
    machines = area.models.machines
    proceeded: list[str] = []
    machines.check(lambda: proceeded.append("first"))
    machines.check(lambda: proceeded.append("second"))
    manual.answer("reconcile_cloud")
    assert (proceeded, len(manual.pending("reconcile_cloud"))) == ([], 1)
    manual.answer("reconcile_cloud")
    assert proceeded == ["first", "second"]


def test_a_second_close_still_reopens_the_dialog(world: CloudWorld) -> None:
    """Start, close, reopen, close: the first Start answered with a changed plan reopens the dialog and plans."""
    area, manual = _manual(world)
    start = area.models.start
    _start(area, manual)
    start.close()
    start.show_dialog()
    start.close()
    write_foreign(world.store, LATE_BATCH_ID)
    manual.answer(LAUNCH)
    note = "Your earlier Start started nothing, as the work changed; check the plan here."
    assert (start.phase, start.reopened, start.notice) == ("planning", True, note)
    manual.answer_all()
    view = start.form.view
    assert view is not None
    assert (start.phase, len(view.batches), start.can_start) == ("ready", 3, True)
