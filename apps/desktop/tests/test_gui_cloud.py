"""The Cloud area's view-models over the fake Runpod API and the simulated worker (plan m6 M6.6).

Driven like a user on `SyncExecutor` (or on a manual executor where the order of answers
matters): plan and start, follow a run from its queue to its end through the poll, stop and
cancel through the backend's confirmation, check the cloud machines and stop a straggler, a
changed plan, an unknown job's button, a `cleanup_pending` refusal. Poll timing uses a manual
timer, never a sleep. The requests the scripted states build are request goldens
(`gui_cloud_*`), which the parity runner also sends through the backend and the CLI.
"""

import json
import subprocess
import sys
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from support.batches import FILM, FIRST_BATCH_ID, build_batch_tree
from support.cloud import CloudArea, CloudWorld, ManualExecutor, cloud_area, cloud_world, documents
from support.credentials import moto_bucket
from support.fakes import FIXED_NOW
from support.golden import GOLDEN_DIR, assert_golden
from support.launch import FOREIGN_BATCH_ID, write_foreign
from support.launch_legs import LaunchLegs, seed_ready_leg
from support.parity import run_parity
from support.runs import RUN_ID, app_pod_name
from support.worker_sim import SimulatedJob, finished, transcribing
from transcribe_contracts.classes import JobRef
from transcribe_ui.api.classes import ErrorBody, ErrorResponse, Request
from transcribe_ui.credentials.classes import CredentialsStatusResponse, CredentialStatus
from transcribe_ui.gui.activity import Activity
from transcribe_ui.gui.cloud.display import RunView, countdown_text, run_view
from transcribe_ui.gui.cloud.startup import RunpodKey, register_startup
from transcribe_ui.gui.cloud.status import status_items
from transcribe_ui.gui.executor import SyncExecutor
from transcribe_ui.gui.signals import StartupHooks
from transcribe_ui.i18n.catalog import activate
from transcribe_ui.state.models import BatchRow

SUBPROCESS_TIMEOUT_S = 60
# Item ids of the seeded batch.
FILM_ID = "01JDZZZZZZZZZZZZZZZZZZZZ01"
PODCAST_ID = "01JDZZZZZZZZZZZZZZZZZZZZ02"
# Two hours of waiting for a machine.
TWO_HOURS_S = 7200
MINUTE = timedelta(minutes=1)
REFRESH = "refresh_status"
LIST = "list_runs"
LAUNCH = "launch_worker"
PLAN = "plan_launch"
CANCEL = "cancel_worker"
RUNS_CHANGED = "runs_changed"
BATCHES_CHANGED = "batches_changed"
# Another computer's second job, written while the start dialog is open.
LATE_BATCH_ID = "batch_20260102_090000"
# The Qt-free modules of the Cloud area.
QT_FREE_MODULES = (
    "transcribe_ui.gui.cloud.area",
    "transcribe_ui.gui.cloud.commands",
    "transcribe_ui.gui.cloud.constants",
    "transcribe_ui.gui.cloud.display",
    "transcribe_ui.gui.cloud.machines",
    "transcribe_ui.gui.cloud.plan_display",
    "transcribe_ui.gui.cloud.poll",
    "transcribe_ui.gui.cloud.ports",
    "transcribe_ui.gui.cloud.run_actions",
    "transcribe_ui.gui.cloud.runs",
    "transcribe_ui.gui.cloud.start",
    "transcribe_ui.gui.cloud.start_form",
    "transcribe_ui.gui.cloud.status",
)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[CloudWorld]:
    """Return a launch leg: the cloud set up, the published batch and another computer's job waiting."""
    with cloud_world(tmp_path) as created:
        yield created


@pytest.fixture
def area(world: CloudWorld) -> CloudArea:
    """Return the Cloud area's view-models on `SyncExecutor` over the world's backend."""
    return cloud_area(world.backend)[0]


def _manual(world: CloudWorld) -> tuple[CloudArea, ManualExecutor]:
    created, manual = cloud_area(world.backend, manual=True)
    assert manual is not None
    return created, manual


def _picked(area: CloudArea, *actions: str) -> list[Request]:
    return [request for request in area.runner.requests if request.action in actions]


def _launch(area: CloudArea, *, wait_s: int | None = None) -> str:
    """Open the start dialog, start; return the run id."""
    start = area.models.start
    start.show_dialog()
    if wait_s is not None:
        start.form.choose_wait(wait_s)
    start.start()
    assert start.phase == "launched", start.error
    current = area.models.runs.current
    assert current is not None
    return current.run.run_id


def _view(area: CloudArea) -> RunView:
    current = area.models.runs.current
    assert current is not None
    return run_view(current)


def _stage(area: CloudArea) -> str:
    return _view(area).stage


def _queued_status(world: CloudWorld) -> str:
    return str(world.cloud.api.queued_pods.listed()[0]["status"])


def _question(area: CloudArea) -> str:
    asked = area.outlet.asked[-1]
    return asked.confirmations[0].message


def _queued_pod(world: CloudWorld) -> str:
    return str(world.cloud.api.queued_pods.listed()[0]["id"])


def test_the_view_models_do_not_load_qt() -> None:
    """The Cloud area's view-models import no Qt."""
    lines = ["import sys", *(f"import {name}" for name in QT_FREE_MODULES)]
    lines.append("assert not [name for name in sys.modules if name.startswith(('PySide6', 'shiboken6'))]")
    completed = subprocess.run(  # noqa: S603 -- the test interpreter on a fixed script
        [sys.executable, "-c", "\n".join(lines)],
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def _wait_then_start(world: CloudWorld, area: CloudArea) -> str:
    """Launch on a queue; the run waits (Cancel), then its queued pod deploys (Stop); return the run id."""
    poll = area.timers.poll
    world.cloud.settings.capacity = False
    run_id = _launch(area, wait_s=TWO_HOURS_S)
    view = _view(area)
    assert (view.stage, view.stop_label, poll.active) == ("waiting", "Stop waiting", True)
    assert area.outlet.signals(BATCHES_CHANGED)[-1] == ((FOREIGN_BATCH_ID, FIRST_BATCH_ID),)
    poll.fire()
    assert _stage(area) == "waiting"
    world.cloud.api.queued_pods.schedule_deploy(_queued_pod(world), world.now())
    world.cloud.clock.advance(MINUTE)
    poll.fire()
    assert (_stage(area), _view(area).stop_label) == ("starting", "Stop")
    return run_id


def _transcribe(world: CloudWorld, area: CloudArea, run_id: str) -> None:
    """The worker reports its first file: the page shows it, the counts, elapsed time, cost and the countdown."""
    transcribing(world.cloud, world.store, run_id, JobRef(batch_id=FIRST_BATCH_ID, item_id=FILM_ID, language="en"))
    area.timers.poll.fire()
    shown = _view(area)
    assert (shown.stage, shown.current_file, shown.current_job, shown.done) == (
        "transcribing",
        f"{FILM} (en)",
        FIRST_BATCH_ID,
        "0/2",
    )
    remaining = countdown_text(shown.finish_at, world.now())
    assert (remaining.endswith(" left"), shown.elapsed, shown.cost, area.outlet.alerts) == (
        True,
        "20 min 0 s",
        "0.09 USD",
        0,
    )


def _finish(world: CloudWorld, area: CloudArea, run_id: str) -> None:
    """The worker finishes and its pod ends: the run is final, the poll stops, the window flashes."""
    jobs = (SimulatedJob(FILM_ID, "en", "ok"), SimulatedJob(PODCAST_ID, "en", "no_speech"))
    finished(world.cloud, world.store, run_id, FIRST_BATCH_ID, jobs)
    area.timers.poll.fire()
    ended = _view(area)
    assert (ended.stage, ended.final, ended.stop_label, ended.finish_at) == ("finished", True, None, None)
    runs = area.models.runs
    assert (area.timers.poll.active, area.outlet.alerts, runs.run_active()) == (False, 1, False)
    assert (FIRST_BATCH_ID,) in [arguments[0] for arguments in area.outlet.signals(BATCHES_CHANGED)]


def test_a_run_followed_from_its_queue_to_its_end(world: CloudWorld, area: CloudArea) -> None:
    """Queued, deployed, transcribing, finished: the poll starts with the launch and stops on its own."""
    area.models.runs.load()
    assert (area.models.runs.current, area.timers.poll.active) == (None, False)
    run_id = _wait_then_start(world, area)
    _transcribe(world, area, run_id)
    _finish(world, area, run_id)
    assert len(area.outlet.signals(RUNS_CHANGED)) == len(_picked(area, REFRESH)) + 1
    assert_golden("gui_cloud_run.json", documents(_picked(area, PLAN, LAUNCH, REFRESH, LIST)))


def test_no_tick_while_one_is_queued_or_running(world: CloudWorld) -> None:
    """A tick sends nothing while the previous check is queued or running; the page says which."""
    area, manual = _manual(world)
    poll = area.models.poll
    sent = poll.tick()
    assert (sent, poll.note(), poll.checking) == (True, "Waiting to check the cloud...", True)
    manual.start(REFRESH)
    area.timers.poll.start()
    area.timers.poll.fire()
    assert (poll.note(), poll.tick(), len(manual.pending(REFRESH))) == ("Checking the cloud...", False, 1)
    manual.answer(REFRESH)
    assert (poll.checking, len(manual.pending(LIST)), poll.tick()) == (False, 1, True)


def _launched_manually(area: CloudArea, manual: ManualExecutor) -> None:
    area.models.start.show_dialog()
    manual.answer(PLAN)
    area.models.start.start()
    manual.answer(LAUNCH)
    manual.answer_all()


@pytest.mark.parametrize("code", ["runs_busy", "cloud_unreachable"])
def test_a_busy_or_unreachable_check_shows_as_retrying(world: CloudWorld, code: str) -> None:
    """`runs_busy` and `cloud_unreachable` show "last updated, retrying", no error; the next tick tries again."""
    area, manual = _manual(world)
    _launched_manually(area, manual)
    poll = area.models.poll
    timer = area.timers.poll
    timer.fire()
    manual.answer(REFRESH)
    manual.answer_all()
    current = area.models.runs.current
    refreshed = None if current is None else current.refreshed_at
    assert refreshed is not None
    timer.fire()
    failed = ErrorResponse(action=REFRESH, error=ErrorBody(code=code, message="It failed."))
    manual.answer(REFRESH, failed)
    manual.answer_all()
    updated = refreshed.astimezone()
    assert poll.note() == f"Last updated {updated:%Y-%m-%d %H:%M:%S}; retrying at the next check."
    timer.fire()
    shown = (poll.shown_error(), area.outlet.errors, timer.active, len(manual.pending(REFRESH)))
    assert shown == (None, [], True, 1)


def test_another_check_error_shows_in_the_page(world: CloudWorld) -> None:
    """An error the next check does not simply retry is shown (with its fix), never as a dialog."""
    area, manual = _manual(world)
    poll = area.models.poll
    poll.tick()
    missing = ErrorResponse(action=REFRESH, error=ErrorBody(code="credentials_missing", message="Missing."))
    manual.answer(REFRESH, missing)
    error = poll.shown_error()
    assert error is not None
    assert (error.code, error.fix is not None, poll.note(), area.outlet.errors) == ("credentials_missing", True, "", [])


def test_stop_and_cancel_ask_the_backend_first(world: CloudWorld, area: CloudArea) -> None:
    """Cancel while waiting: declined, nothing changes; confirmed, the run is cancelled and the poll stops."""
    world.cloud.settings.capacity = False
    _launch(area)
    outlet = area.outlet
    outlet.accept = False
    area.models.actions.stop()
    assert _question(area).startswith("Stop waiting for a cloud machine?")
    assert (_stage(area), _queued_status(world)) == ("waiting", "QUEUED")
    outlet.accept = True
    area.models.actions.stop()
    assert (_stage(area), area.timers.poll.active, area.models.actions.stopping) == ("cancelled", False, False)
    assert_golden("gui_cloud_cancel.json", documents(_picked(area, PLAN, LAUNCH, CANCEL)))


def test_stop_while_starting(world: CloudWorld, area: CloudArea) -> None:
    """A started run shows "Stop"; the question says that finished subtitles are kept; confirmed, it stops."""
    _launch(area)
    assert _view(area).stop_label == "Stop"
    area.models.actions.stop()
    assert _question(area).startswith("Stop the cloud machine now? Subtitles already finished are kept")
    assert (_stage(area), world.cloud.api.pods.listed()) == ("cancelled", [])


def test_the_start_up_checks_and_a_straggler(world: CloudWorld, area: CloudArea) -> None:
    """At start-up a straggler shows as attention (status bar link); Terminate asks, stops it, and checks again."""
    world.cloud.api.pods.add(app_pod_name(RUN_ID, FIXED_NOW))
    hooks = StartupHooks()
    register_startup(hooks, area.models, RunpodKey(SyncExecutor(area.runner)))
    done: list[bool] = []
    hooks.run(lambda: done.append(True))
    machines = area.models.machines
    started = [action for action in area.runner.actions() if action != LIST]
    assert (done, started) == ([True], ["credentials_status", "reconcile_cloud", REFRESH])
    assert (machines.attention, [row.action_label for row in machines.rows]) == (True, ["Terminate"])
    shown = status_items(area.models, Activity(SyncExecutor(area.runner)), world.now())
    assert [found.target for found in shown] == ["machines"]
    machines.stop(machines.rows[0])
    assert (machines.attention, machines.rows, len(area.outlet.asked)) == (False, (), 1)
    assert_golden("gui_cloud_machines.json", documents(_picked(area, "reconcile_cloud", "terminate_pod")))


def test_no_cloud_check_without_a_runpod_key(world: CloudWorld) -> None:
    """A missing Runpod key leaves out `reconcile_cloud` and `refresh_status`; the sequence still finishes."""
    area, manual = _manual(world)
    hooks = StartupHooks()
    register_startup(hooks, area.models, RunpodKey(manual))
    done: list[bool] = []
    hooks.run(lambda: done.append(True))
    manual.answer(LIST)
    key = CredentialStatus(name="runpod_api_key", environment_variable="RUNPOD_API_KEY", source="missing")
    manual.answer("credentials_status", CredentialsStatusResponse(keyring="available", credentials=(key,)))
    assert (done, [held.request.action for held in manual.held]) == ([True], [LIST, "credentials_status"])


def test_the_plan_lists_another_computers_job(area: CloudArea) -> None:
    """The plan shows each job; another computer's has "Delete from cloud", which only emits its signal."""
    start = area.models.start
    start.show_dialog()
    view = start.form.view
    assert view is not None
    assert [(row.batch_id, row.known) for row in view.batches] == [(FOREIGN_BATCH_ID, False), (FIRST_BATCH_ID, True)]
    assert view.summary == "3 transcriptions in 2 jobs, 2 h 1 min of audio."
    plans = len(_picked(area, PLAN))
    start.plan_again()
    assert len(_picked(area, PLAN)) == plans + 1
    start.close()
    start.plan_again()
    assert len(_picked(area, PLAN)) == plans + 1, "a closed dialog does not plan"


def test_a_changed_plan_is_shown_and_confirmed(world: CloudWorld, area: CloudArea) -> None:
    """A job uploaded while the dialog shows the plan: Start shows the new plan; the next Start confirms it."""
    start = area.models.start
    start.show_dialog()
    first = start.form.fingerprint
    write_foreign(world.store, LATE_BATCH_ID)
    start.start()
    view = start.form.view
    assert view is not None
    assert (start.phase, start.form.plan_changed, len(view.batches)) == ("ready", True, 3)
    assert start.form.fingerprint != first
    start.start()
    assert start.phase == "launched"
    assert_golden("gui_cloud_changed_plan.json", documents(_picked(area, PLAN, LAUNCH)))


def test_test_mode_and_the_wait_reach_the_launch(area: CloudArea) -> None:
    """Test mode plans again (its fingerprint differs); the launch carries the wait and test mode chosen."""
    start = area.models.start
    start.show_dialog()
    assert [seconds for seconds, _label in start.form.wait_choices()][:2] == [300, 900]
    start.plan_again(test_mode=True)
    start.form.choose_wait(TWO_HOURS_S)
    start.start()
    launched = _picked(area, LAUNCH)[-1]
    assert launched.model_dump(exclude={"confirm_fingerprint"}) == {
        "action": LAUNCH,
        "wait_s": TWO_HOURS_S,
        "test_mode": True,
    }
    assert_golden("gui_cloud_launch.json", documents(_picked(area, PLAN, LAUNCH)))


def test_an_interrupted_cleanup_links_to_the_job(world: CloudWorld, area: CloudArea) -> None:
    """`cleanup_pending` shows in the dialog with "Open the job"; the fix closes the dialog."""
    with world.ports.state.session() as session:
        row = session.get(BatchRow, FIRST_BATCH_ID)
        assert row is not None
        row.payload = {**row.payload, "cleanup_pending": "audio"}
    start = area.models.start
    start.show_dialog()
    error = start.error
    assert error is not None
    assert error.fix is not None
    assert (error.code, error.fix.kind, error.fix.argument, start.can_start) == (
        "cleanup_pending",
        "open_batch",
        FIRST_BATCH_ID,
        False,
    )


def test_the_countdown_and_the_texts_in_hungarian(world: CloudWorld, area: CloudArea) -> None:
    """The countdown never goes negative ("finishing" at 0); the run's texts follow the language."""
    finish = world.now() + timedelta(minutes=5)
    shown = [
        countdown_text(finish, world.now()),
        countdown_text(finish, finish),
        countdown_text(finish, finish + MINUTE),
    ]
    assert (shown, countdown_text(None, finish)) == (["5 min 0 s left", "Finishing", "Finishing"], "")
    world.cloud.settings.capacity = False
    _launch(area)
    activate("hu")
    view = _view(area)
    assert (view.stage_text, view.stop_label) == ("Várakozás szabad felhőgépre", "Várakozás leállítása")
    assert countdown_text(finish, world.now()) == "5 p 0 mp van hátra"


@pytest.mark.parametrize("golden", sorted(GOLDEN_DIR.glob("gui_cloud_*.json")), ids=lambda path: path.stem)
def test_gui_requests_are_valid_cli_requests(tmp_path: Path, golden: Path) -> None:
    """Each request the Cloud area built is valid for the CLI and answers alike from the backend and the CLI."""
    templates = [json.dumps(document) for document in json.loads(golden.read_text(encoding="utf-8"))]
    with moto_bucket():
        exchanges = run_parity(tmp_path, templates, prepare=build_batch_tree, ports=LaunchLegs(seed=seed_ready_leg))
    codes = [json.loads(exchange.response).get("error", {}).get("code") for exchange in exchanges]
    assert "invalid_request" not in codes


def test_a_failed_stop_reloads_the_runs(world: CloudWorld) -> None:
    """A stop that fails still reloads the run list (it may have changed the run before it failed)."""
    area, manual = _manual(world)
    _launched_manually(area, manual)
    area.models.actions.stop()
    manual.answer(CANCEL)
    lists = len(manual.pending(LIST))
    failed = ErrorResponse(action=CANCEL, error=ErrorBody(code="stop_unverified", message="Not yet."))
    manual.answer(CANCEL, failed)
    assert (len(manual.pending(LIST)), len(area.outlet.errors)) == (lists + 1, 1)


def test_a_check_error_shows_in_the_status_bar(world: CloudWorld) -> None:
    """A check of the runs that failed for good shows in the status bar; a retrying one does not."""
    area, manual = _manual(world)
    poll = area.models.poll
    activity = Activity(SyncExecutor(area.runner))
    poll.tick()
    manual.answer(REFRESH, ErrorResponse(action=REFRESH, error=ErrorBody(code="runs_busy", message="Busy.")))
    assert status_items(area.models, activity, world.now()) == ()
    poll.tick()
    manual.answer(REFRESH, ErrorResponse(action=REFRESH, error=ErrorBody(code="credentials_invalid", message="No.")))
    assert [found.target for found in status_items(area.models, activity, world.now())] == ["cloud"]
