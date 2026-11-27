"""`refresh_status` (plan m5 M5.12): a run followed from its queued pod to its end, results and run objects.

Batches are seeded through the state store, their audio in moto; Runpod is the fake API and the
worker's objects are written into moto as the worker writes them.
"""

from datetime import timedelta
from types import MappingProxyType

import pytest

from support.batches import FILM, FIRST_BATCH_ID, PODCAST
from support.launch import launch_run, pods_of, queued_of, run_row, set_up_cloud
from support.monitor import (
    WORKER_LOG,
    end_pod,
    job_statuses,
    refresh,
    refreshed_run,
    run_objects,
    write_done,
    write_log,
    write_result,
    write_status,
)
from support.runs import READY, RunsWorld, batch_state, seed_uploaded
from transcribe_contracts.classes import JobRef
from transcribe_ui.runs.collect import worker_log_path
from transcribe_ui.runs.constants import DESKTOP_DEADLINE_MARGIN_S, WAIT_MIN_S

# Two hours of waiting for a machine.
TWO_HOURS_S = 7200
# A minute.
MINUTE = timedelta(minutes=1)
# How long the simulated worker transcribes before the progress is read.
TRANSCRIBING_S = 1200
# Its elapsed time then (the pod was created at its first sighting) and its cost at 0.27 USD/h.
TRANSCRIBING_ELAPSED_S = 1200
TRANSCRIBING_COST_USD = 0.09
# Objects the worker leaves under `runs/<run_id>/`: its status and its log.
WORKER_OBJECTS = 2
# Item ids of the seeded batch, by file name (as `seed_uploading_batch` stores them).
SEEDED_IDS = MappingProxyType({FILM: "01JDZZZZZZZZZZZZZZZZZZZZ01", PODCAST: "01JDZZZZZZZZZZZZZZZZZZZZ02"})


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest held (the launch publishes it)."""
    set_up_cloud(runs_world)
    seed_uploaded(runs_world, held=True)
    return runs_world


def _wait_then_deploy(world: RunsWorld, run_id: str) -> None:
    """The run waits for a machine, then its queued pod deploys and the worker starts."""
    waiting = refreshed_run(world, run_id)
    assert (waiting.stage, waiting.run.status, waiting.pod_status, waiting.elapsed_s) == (
        "waiting",
        "queued",
        None,
        None,
    )
    world.cloud.api.queued_pods.schedule_deploy(queued_of(world)[0][0], world.now())
    world.cloud.clock.advance(MINUTE)
    starting = refreshed_run(world, run_id)
    payload = run_row(world, run_id)[1]
    deadline = world.now() + timedelta(seconds=payload.max_runtime_s + DESKTOP_DEADLINE_MARGIN_S)
    assert (starting.stage, starting.run.status, starting.pod_status, starting.first_running_at) == (
        "starting",
        "running",
        "RUNNING",
        world.now(),
    )
    assert (starting.deadline, payload.pod_seen, starting.price_per_hour_usd) == (deadline, True, 0.27)


def _transcribe(world: RunsWorld, run_id: str) -> None:
    """The worker reports the job it transcribes; elapsed time and cost so far grow."""
    current = JobRef(batch_id=FIRST_BATCH_ID, item_id=SEEDED_IDS[FILM], language="en")
    write_status(world.store, run_id, "transcribing", world.now(), current=current)
    world.cloud.clock.advance(timedelta(seconds=TRANSCRIBING_S))
    transcribing = refreshed_run(world, run_id)
    assert transcribing.worker is not None
    assert transcribing.worker.current is not None
    assert (transcribing.stage, transcribing.worker.current.item_id) == ("transcribing", SEEDED_IDS[FILM])
    assert (transcribing.elapsed_s, transcribing.cost_so_far_usd) == (TRANSCRIBING_ELAPSED_S, TRANSCRIBING_COST_USD)


def _finish_work(world: RunsWorld, run_id: str) -> None:
    """The worker writes its results, `_done.json`, its final status and its log; its pod still exists."""
    now = world.now()
    write_result(world.store, FIRST_BATCH_ID, SEEDED_IDS[FILM], run_id, now)
    write_result(world.store, FIRST_BATCH_ID, SEEDED_IDS[PODCAST], run_id, now, status="no_speech")
    write_done(world.store, FIRST_BATCH_ID, run_id, now)
    write_status(world.store, run_id, "finished", now, done_jobs=2)
    write_log(world.store, run_id)
    stopping = refresh(world)
    phases = [each.phase for each in stopping.batches]
    assert (stopping.runs[0].stage, phases) == ("stopping", ["transcribed"])
    statuses = job_statuses(world, FIRST_BATCH_ID)
    assert statuses == {SEEDED_IDS[FILM]: "ok", SEEDED_IDS[PODCAST]: "no_speech"}
    assert len(run_objects(world, run_id)) == WORKER_OBJECTS, "kept while the pod still exists"


def _pod_gone(world: RunsWorld, run_id: str) -> None:
    """The worker's pod terminates itself: the run is finished, its objects kept locally and deleted."""
    end_pod(world, pods_of(world)[0][0])
    world.cloud.clock.advance(MINUTE)
    ended = refreshed_run(world, run_id)
    log = worker_log_path(world.ports.state.paths.log_dir, run_id)
    assert (ended.run.status, ended.stage, ended.end_reason, ended.ended_at) == (
        "finished",
        "finished",
        "worker_finished",
        world.now(),
    )
    assert (run_objects(world, run_id), ended.worker_log, log.read_text()) == ([], str(log), WORKER_LOG)
    final = run_row(world, run_id)[1].final_status
    assert final is not None
    assert (final.state, final.done_jobs, refresh(world).runs) == ("finished", 2, ())


def test_queued_to_finished(world: RunsWorld) -> None:
    """Queued, deployed, starting, transcribing, finished: results recorded, objects kept and deleted."""
    world.cloud.settings.capacity = False
    run_id = launch_run(world, wait_s=TWO_HOURS_S).run_id
    _wait_then_deploy(world, run_id)
    _transcribe(world, run_id)
    _finish_work(world, run_id)
    _pod_gone(world, run_id)


def test_queue_expired(world: RunsWorld) -> None:
    """The queued pod expires without a machine: the run fails, its batch stays `ready`."""
    world.cloud.settings.capacity = False
    run_id = launch_run(world, wait_s=WAIT_MIN_S).run_id
    world.cloud.clock.advance(timedelta(seconds=WAIT_MIN_S) + MINUTE)
    expired = refreshed_run(world, run_id)
    assert (expired.run.status, expired.stage, expired.end_reason, queued_of(world)[0][1]) == (
        "failed",
        "failed",
        "queue_ended",
        "EXPIRED",
    )
    assert batch_state(world) == READY


def test_worker_failed(world: RunsWorld) -> None:
    """The worker reports `failed` and its pod goes away: the run fails, the worker's error is kept."""
    run_id = launch_run(world).run_id
    write_status(world.store, run_id, "failed", world.now())
    running = refresh(world).runs[0]
    assert (running.run.status, running.stage) == ("running", "stopping")
    end_pod(world, pods_of(world)[0][0])
    failed = refreshed_run(world, run_id)
    final = run_row(world, run_id)[1].final_status
    assert final is not None
    assert (failed.run.status, failed.end_reason, final.error, run_objects(world, run_id)) == (
        "failed",
        "worker_failed",
        "the GPU is gone",
        [],
    )


def test_pod_gone_without_report(world: RunsWorld) -> None:
    """A pod that goes away without a final worker report: the run fails (`pod_gone`)."""
    run_id = launch_run(world).run_id
    refresh(world)
    end_pod(world, pods_of(world)[0][0])
    failed = refreshed_run(world, run_id)
    assert (failed.run.status, failed.end_reason, failed.worker) == ("failed", "pod_gone", None)
