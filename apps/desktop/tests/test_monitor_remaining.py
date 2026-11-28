"""The run's progress and remaining time from `refresh_status` (plan m6 M6.12, architecture §3.7).

The seeded batch holds `FILM` (90 min) and `PODCAST` (1 min), one English job each. The worker's
reports and results are written into moto with chosen times; the manual clock is the backend's
(the simulated worker's runs a fixed offset ahead in some tests: the estimate uses the worker's
times only against each other). Between refreshes the remaining time counts down, so the finish
stays where it was.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType

import pytest

from support.batches import FILM, FILM_SECONDS, FIRST_BATCH_ID, PODCAST
from support.credentials import store_request
from support.launch import (
    CANCEL,
    FOREIGN_SECONDS,
    launch_run,
    ok_as,
    pods_of,
    run_row,
    send,
    set_up_cloud,
    write_foreign,
)
from support.monitor import ENGINE, LIST_RUNS, REFRESH, WORKER_POD_ID, end_pod, refreshed_run, write_done, write_result
from support.runs import JSON_TYPE, OTHER_RUN_ID, RunsWorld, seed_uploaded
from transcribe_contracts.classes import JobRef, JobResult, RunState, RunStatus
from transcribe_contracts.classes import JobStatus as ResultStatus
from transcribe_contracts.layout import result_json_key, run_status_key
from transcribe_ui.media.constants import FAKE_DEFAULT_DURATION_S
from transcribe_ui.runs.constants import (
    CAREFUL_WEIGHT,
    PROGRESS_FRACTION_DECIMALS,
    RUNTIME_JOB_OVERHEAD_S,
    RUNTIME_REAL_TIME_FACTOR,
    STARTUP_ALLOWANCE_S,
)
from transcribe_ui.runs.remaining import done_share
from transcribe_ui.runs.responses import CancelWorkerResponse, ListRunsResponse, RunProgress
from transcribe_ui.state.models import JobRow, RunRow

# Length of `PODCAST` (the fake's default probe).
PODCAST_SECONDS = FAKE_DEFAULT_DURATION_S
# No move of the clock.
NO_MOVE = timedelta(0)
# How long the clock moves on between refreshes.
STEP = timedelta(minutes=5)
# The worker's processing time per second of audio in the simulated runs.
WORKER_FACTOR = 0.25
# A bucket that does not exist (the R2 credentials point nowhere).
MISSING_BUCKET = "no-such-bucket"
# A worker report whose content cannot be read.
UNREADABLE_REPORT = b"{}"
# How far the simulated worker's clock is ahead of the backend clock in the shifted tests.
WORKER_OFFSET = timedelta(hours=1)
# The worker clock offsets each phase is checked with.
OFFSETS = (NO_MOVE, WORKER_OFFSET)
# How long ago another run worked on a job (by the worker's clock).
LONG_AGO = timedelta(hours=9)
# Followed audio of a long run, and the audio still pending at its very end (seconds).
LONG_RUN_S = 1_000_000
LAST_SECOND_S = 1
# Worker state while it transcribes.
TRANSCRIBING: RunState = "transcribing"
# Item ids of the seeded batch, by file name (as `seed_uploading_batch` stores them).
SEEDED_IDS = MappingProxyType({FILM: "01JDZZZZZZZZZZZZZZZZZZZZ01", PODCAST: "01JDZZZZZZZZZZZZZZZZZZZZ02"})


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest held (the launch publishes it)."""
    set_up_cloud(runs_world)
    seed_uploaded(runs_world, held=True)
    return runs_world


def _make_careful(world: RunsWorld, item_id: str) -> None:
    """Switch the item's job to `careful` (before the launch)."""
    with world.ports.state.session() as session:
        row = session.query(JobRow).filter(JobRow.item_id == item_id).one()
        row.payload = {**row.payload, "mode": "careful"}


@dataclass(frozen=True)
class Worker:
    """The simulated worker of a run, its clock `offset` ahead of the backend's."""

    world: RunsWorld
    run_id: str
    offset: timedelta = NO_MOVE

    def now(self) -> datetime:
        """Return the worker's time."""
        return self.world.now() + self.offset

    def report(self, state: RunState = TRANSCRIBING, *, working: bool = True) -> None:
        """Write the worker's report as of now; `working`: it names the film as its current job."""
        started = self.now() - timedelta(hours=1)
        current = JobRef(batch_id=FIRST_BATCH_ID, item_id=SEEDED_IDS[FILM], language="en")
        report = RunStatus(
            run_id=self.run_id,
            pod_id=WORKER_POD_ID,
            state=state,
            started_at=started,
            updated_at=self.now(),
            deadline_at=started + timedelta(hours=3),
            current=current if working and state == TRANSCRIBING else None,
            done_jobs=0,
            total_jobs=2,
        )
        self.world.store.write(run_status_key(self.run_id), report.model_dump_json().encode(), JSON_TYPE)

    def done(self, name: str, spent_s: float, status: ResultStatus = "ok") -> None:
        """Write the result of the file's job: this run worked on it for `spent_s` until now."""
        self.write_result(name, spent_s, status, self.run_id)

    def write_result(self, name: str, spent_s: float, status: ResultStatus, run_id: str) -> None:
        """Write the result of the file's job as `run_id` wrote it, finished `spent_s` after it started."""
        finished = self.now() if run_id == self.run_id else self.now() - LONG_AGO
        item_id = SEEDED_IDS[name]
        job_result = JobResult(
            batch_id=FIRST_BATCH_ID,
            item_id=item_id,
            language="en",
            mode="normal",
            run_id=run_id,
            status=status,
            error="decoding failed" if status == "failed" else None,
            aligned=status == "ok",
            cue_count=1 if status == "ok" else 0,
            audio_duration_s=FILM_SECONDS if name == FILM else PODCAST_SECONDS,
            started_at=finished - timedelta(seconds=spent_s),
            finished_at=finished,
            engine=ENGINE,
        )
        key = result_json_key(FIRST_BATCH_ID, item_id, "en")
        self.world.store.write(key, job_result.model_dump_json().encode(), JSON_TYPE)


def _job_runtime_s(duration_s: float, weight: int = 1) -> float:
    """Return a job's runtime by the launch estimate's factors (without the margin)."""
    return (duration_s * RUNTIME_REAL_TIME_FACTOR + RUNTIME_JOB_OVERHEAD_S) * weight


# The launch-factor runtime of the seeded batch.
SEEDED_RUNTIME_S = _job_runtime_s(FILM_SECONDS) + _job_runtime_s(PODCAST_SECONDS)


def _progress(run: RunProgress) -> tuple[float | None, int | None, datetime | None]:
    return run.progress_fraction, run.remaining_estimate_s, run.estimated_finish_at


def _fraction(done_s: float, total_s: float) -> float:
    """Return the done share as the backend shows it: rounded down to its decimals."""
    scale = 10**PROGRESS_FRACTION_DECIMALS
    return int(done_s * scale / total_s) / scale


def _later(world: RunsWorld, seconds: float) -> datetime:
    return world.now() + timedelta(seconds=seconds)


@pytest.fixture
def run_id(world: RunsWorld) -> str:
    """Launch on a free machine; return the run id."""
    return launch_run(world).run_id


@pytest.fixture
def worker(world: RunsWorld, run_id: str) -> Worker:
    """The launched run's worker, on the backend clock."""
    return Worker(world, run_id)


def _estimate(world: RunsWorld, run_id: str) -> tuple[float | None, int | None, datetime | None]:
    """Refresh; return the run's progress fields."""
    return _progress(refreshed_run(world, run_id))


def _reported(worker: Worker, after: timedelta = NO_MOVE, *, working: bool = True) -> RunProgress:
    """Move the clock on by `after`, write a `transcribing` report as of then, refresh; return the run."""
    worker.world.cloud.clock.advance(after)
    worker.report(working=working)
    return refreshed_run(worker.world, worker.run_id)


def _listed(world: RunsWorld) -> RunProgress:
    runs = ok_as(send(world, LIST_RUNS), ListRunsResponse).runs
    assert len(runs) == 1
    return runs[0]


@pytest.mark.parametrize("offset", OFFSETS)
def test_before_the_first_file(world: RunsWorld, run_id: str, offset: timedelta) -> None:
    """Until a job starts: the launch factors and the start-up left, then the time since the first job began.

    A worker that reports `transcribing` before naming a job is still loading its model. The finish
    stays put across refreshes in both phases, whatever the worker's clock says.
    """
    worker = Worker(world, run_id, offset)
    assert run_row(world, run_id)[1].weighted_known_duration_s == FILM_SECONDS + PODCAST_SECONDS
    remaining = round(STARTUP_ALLOWANCE_S + SEEDED_RUNTIME_S)
    first = refreshed_run(world, run_id)
    finish = _later(world, remaining)
    assert (first.stage, _progress(first)) == ("starting", (0, remaining, finish))
    world.cloud.clock.advance(STEP)
    starting = _estimate(world, run_id)
    loading = _progress(_reported(worker, STEP, working=False))
    assert (starting, loading) == (
        (0, remaining - STEP.total_seconds(), finish),
        (0, remaining - 2 * STEP.total_seconds(), finish),
    )
    transcribing = _reported(worker, STEP)
    finish = _later(world, round(SEEDED_RUNTIME_S))
    assert (transcribing.stage, _progress(transcribing)) == (TRANSCRIBING, (0, round(SEEDED_RUNTIME_S), finish))
    expected = (0, round(SEEDED_RUNTIME_S - STEP.total_seconds()), finish)
    assert _progress(_reported(worker, STEP)) == expected


@pytest.mark.parametrize("offset", OFFSETS)
def test_a_long_file_after_a_short_one(world: RunsWorld, run_id: str, offset: timedelta) -> None:
    """The pace of the done file applies to the pending audio, less the time on the file in progress.

    The remaining time shrinks while the long file runs, the finish does not drift (whatever the
    worker's clock says), and `list_runs` later shows the last refresh's values unchanged.
    """
    worker = Worker(world, run_id, offset)
    worker.report()
    worker.done(PODCAST, PODCAST_SECONDS * WORKER_FACTOR)
    first = _reported(worker)
    remaining = round(FILM_SECONDS * WORKER_FACTOR)
    finish = _later(world, remaining)
    total = FILM_SECONDS + PODCAST_SECONDS
    assert _progress(first) == (_fraction(PODCAST_SECONDS, total), remaining, finish)
    later = _reported(worker, STEP)
    assert _progress(later) == (_fraction(PODCAST_SECONDS, total), remaining - STEP.total_seconds(), finish)
    world.cloud.clock.advance(STEP)
    assert _listed(world) == later


def test_a_failed_job_is_done_but_sets_no_pace(world: RunsWorld, worker: Worker) -> None:
    """A failed job counts as done; its time does not measure the pace, so the launch factors stay."""
    worker.report()
    world.cloud.clock.advance(STEP)
    worker.done(FILM, 1, status="failed")
    remaining = round(_job_runtime_s(PODCAST_SECONDS))
    expected = (_fraction(FILM_SECONDS, FILM_SECONDS + PODCAST_SECONDS), remaining, _later(world, remaining))
    assert _progress(_reported(worker)) == expected


def test_another_runs_result(world: RunsWorld, worker: Worker) -> None:
    """A job another run finished long ago counts as done, but neither sets the pace nor starts the file in progress."""
    _reported(worker)
    world.cloud.clock.advance(timedelta(seconds=WORKER_FACTOR * PODCAST_SECONDS))
    worker.write_result(FILM, FILM_SECONDS * 2, "ok", OTHER_RUN_ID)
    remaining = round(_job_runtime_s(PODCAST_SECONDS) - WORKER_FACTOR * PODCAST_SECONDS)
    expected = (_fraction(FILM_SECONDS, FILM_SECONDS + PODCAST_SECONDS), remaining, _later(world, remaining))
    assert _progress(_reported(worker)) == expected


def test_careful_jobs_count_twice(world: RunsWorld) -> None:
    """A `careful` job weighs twice in the followed audio, the done share, the launch factors and the pace."""
    _make_careful(world, SEEDED_IDS[FILM])
    run_id = launch_run(world).run_id
    worker = Worker(world, run_id)
    film_s = FILM_SECONDS * CAREFUL_WEIGHT
    total = film_s + PODCAST_SECONDS
    assert run_row(world, run_id)[1].weighted_known_duration_s == total
    runtime_s = _job_runtime_s(FILM_SECONDS, CAREFUL_WEIGHT) + _job_runtime_s(PODCAST_SECONDS)
    assert _reported(worker).remaining_estimate_s == round(runtime_s)
    world.cloud.clock.advance(timedelta(seconds=film_s * WORKER_FACTOR))
    worker.done(FILM, film_s * WORKER_FACTOR, status="no_speech")
    expected = (_fraction(film_s, total), round(PODCAST_SECONDS * WORKER_FACTOR))
    assert _progress(_reported(worker))[:2] == expected


def test_another_computers_batch(world: RunsWorld) -> None:
    """Another computer's covered batch adds its launch-factor runtime until the pace is measured."""
    write_foreign(world.store)
    run_id = launch_run(world).run_id
    foreign_s = _job_runtime_s(FOREIGN_SECONDS)
    assert run_row(world, run_id)[1].unknown_runtime_s == foreign_s
    assert _reported(Worker(world, run_id)).remaining_estimate_s == round(SEEDED_RUNTIME_S + foreign_s)


def test_all_done(world: RunsWorld, run_id: str, worker: Worker) -> None:
    """Every job done: 1 and nothing left while the pod stops; once final, no remaining time."""
    world.cloud.clock.advance(STEP)
    for name in (FILM, PODCAST):
        write_result(world.store, FIRST_BATCH_ID, SEEDED_IDS[name], run_id, world.now())
    write_done(world.store, FIRST_BATCH_ID, run_id, world.now())
    worker.report("finished")
    stopping = refreshed_run(world, run_id)
    assert (stopping.stage, _progress(stopping)) == ("stopping", (1, 0, world.now()))
    end_pod(world, pods_of(world)[0][0])
    world.cloud.clock.advance(STEP)
    ended = refreshed_run(world, run_id)
    assert (ended.stage, _progress(ended)) == ("finished", (1, None, None))
    assert _listed(world) == ended


def test_cancel_clears_the_remaining_time(world: RunsWorld, run_id: str, worker: Worker) -> None:
    """A run stopped by the user keeps its done share; its remaining time and finish are gone."""
    assert _reported(worker, STEP).remaining_estimate_s is not None
    ok_as(send(world, CANCEL, run_id=run_id), CancelWorkerResponse)
    listed = _listed(world)
    assert (listed.stage, _progress(listed)) == ("cancelled", (0, None, None))


def test_waiting_for_a_machine(world: RunsWorld) -> None:
    """While the run waits for a machine, its remaining time is unknown."""
    world.cloud.settings.capacity = False
    run_id = launch_run(world).run_id
    waiting = refreshed_run(world, run_id)
    assert (waiting.stage, _progress(waiting)) == ("waiting", (0, None, None))


def test_unreadable_report(world: RunsWorld, run_id: str) -> None:
    """A worker report that cannot be read gives no remaining time; the done share stays known."""
    world.store.write(run_status_key(run_id), UNREADABLE_REPORT, JSON_TYPE)
    assert _estimate(world, run_id) == (0, None, None)


def test_nothing_known(world: RunsWorld, run_id: str, worker: Worker) -> None:
    """A run without followed audio (launched before it was kept) has no progress at all."""
    with world.ports.state.session() as session:
        row = session.get(RunRow, run_id)
        assert row is not None
        row.payload = {**row.payload, "weighted_known_duration_s": None}
    assert _progress(_reported(worker)) == (None, None, None)


def test_failed_refresh_leaves_no_stale_estimate(world: RunsWorld, worker: Worker) -> None:
    """A refresh that fails after storing its time leaves the remaining time unknown, not stale."""
    assert _reported(worker, STEP).remaining_estimate_s is not None
    world.cloud.clock.advance(STEP)
    broken = world.leg.backend.execute_json(store_request(r2_bucket=MISSING_BUCKET))
    assert (broken.status, send(world, REFRESH).status) == ("ok", "error")
    listed = _listed(world)
    assert (listed.refreshed_at, _progress(listed)) == (world.now(), (0, None, None))


def test_done_share_is_rounded_down() -> None:
    """With one second of a long run pending, the done share stays below 1 (1 means all done)."""
    assert done_share(LAST_SECOND_S, LONG_RUN_S) == 1 - 10**-PROGRESS_FRACTION_DECIMALS
    assert done_share(0, LONG_RUN_S) == 1
