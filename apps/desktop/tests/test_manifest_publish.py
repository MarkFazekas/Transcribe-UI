"""The manifest held back during a run, `publish_held_manifests`, and races with a launch (plan m5 M5.9).

The `uploading` batch is seeded through the state store (the upload is M5.10), its audio in moto.
"""

import threading

import pytest

from support.batches import FIRST_BATCH_ID, PODCAST
from support.runs import (
    MANIFEST,
    OUTAGE,
    RUN_ID,
    BatchState,
    RunsWorld,
    app_pod_name,
    batch_state,
    seed_run,
    seed_uploaded,
    set_item_phase,
    set_run_status,
)
from transcribe_ui.runpod.constants import POD_TERMINATED
from transcribe_ui.runs.errors import ActivityUnknownError
from transcribe_ui.state.errors import LockOrderError, RunsBusyError
from transcribe_ui.upload.publish import publish_held_manifests

# Seconds a test thread waits for an event at most.
THREAD_TIMEOUT_S = 10
# How long the simulated launch holds the runs lock: shorter than the test backends' wait (0.5 s).
SHORT_HOLD_S = 0.2
# How long the simulated launch holds the runs lock past that wait.
LONG_HOLD_S = 2.0
# Upload complete, no manifest yet.
UPLOADING: BatchState = ("uploading", False, False)
# Upload complete, manifest held back.
HELD: BatchState = ("uploading", True, False)
# Manifest published.
READY: BatchState = ("ready", False, True)


def test_held_during_a_run_and_published_after_it(runs_world: RunsWorld) -> None:
    """A live pod keeps the manifest back; once it is terminated, `publish_held_manifests` publishes it."""
    seed_uploaded(runs_world)
    pod = runs_world.cloud.api.pods.add(app_pod_name(RUN_ID, runs_world.now()))
    step = runs_world.step()
    assert (step.outcome, [source.kind for source in step.activity], batch_state(runs_world)) == ("held", ["pod"], HELD)
    still = runs_world.publish_held()
    assert (still.held, still.published, runs_world.store.head(MANIFEST)) == ((FIRST_BATCH_ID,), (), None)
    pod["status"] = POD_TERMINATED
    done = runs_world.publish_held()
    assert (done.published, done.held, batch_state(runs_world)) == ((FIRST_BATCH_ID,), (), READY)


def test_nothing_held_needs_no_cloud(runs_world: RunsWorld) -> None:
    """Without held batches, `publish_held_manifests` does not even check the activity."""
    runs_world.cloud.api.fail("GET", "/v2/pods", *OUTAGE)
    assert runs_world.publish_held().published == ()


def test_unreadable_cloud_holds_the_manifest(runs_world: RunsWorld) -> None:
    """Fail closed: the step marks the manifest held and raises; a later publish resumes it."""
    seed_uploaded(runs_world)
    runs_world.cloud.api.fail("GET", "/v2/pods", *OUTAGE)
    with pytest.raises(ActivityUnknownError):
        runs_world.step()
    after_step = batch_state(runs_world)
    runs_world.cloud.api.fail("GET", "/v2/queued-pods", *OUTAGE)
    with pytest.raises(ActivityUnknownError):
        runs_world.publish_held()
    unpublished = runs_world.store.head(MANIFEST) is None
    assert (after_step, unpublished, runs_world.publish_held().published) == (HELD, True, (FIRST_BATCH_ID,))


def _hold_batch(world: RunsWorld, entered: threading.Event, release: threading.Event) -> None:
    """Hold the batch's lock like another request working on the batch, until released."""
    with world.ports.state.locks.batch(FIRST_BATCH_ID):
        entered.set()
        release.wait(THREAD_TIMEOUT_S)


def test_publish_skips_a_batch_whose_lock_is_held(runs_world: RunsWorld) -> None:
    """Another request working on the batch: it is skipped (never waited for) and stays held."""
    seed_uploaded(runs_world, held=True)
    entered, release = threading.Event(), threading.Event()
    holder = threading.Thread(target=_hold_batch, args=(runs_world, entered, release))
    holder.start()
    assert entered.wait(THREAD_TIMEOUT_S)
    skipped = runs_world.publish_held()
    release.set()
    holder.join(THREAD_TIMEOUT_S)
    assert (skipped.busy, skipped.published, batch_state(runs_world)) == ((FIRST_BATCH_ID,), (), HELD)
    assert runs_world.publish_held().published == (FIRST_BATCH_ID,)


def test_publish_never_includes_an_item_in_review(runs_world: RunsWorld) -> None:
    """A held batch with an item in review is not published (its upload publishes it later)."""
    ids = seed_uploaded(runs_world, held=True)
    set_item_phase(runs_world, ids[PODCAST], "reviewing")
    skipped = runs_world.publish_held()
    assert (skipped.incomplete, skipped.published, batch_state(runs_world)) == ((FIRST_BATCH_ID,), (), HELD)


def test_publish_needs_the_runs_lock(runs_world: RunsWorld) -> None:
    """Publishing without the runs lock is a programming error (a launch could start meanwhile)."""
    seed_uploaded(runs_world, held=True)
    with runs_world.access() as cloud, pytest.raises(LockOrderError):
        publish_held_manifests(runs_world.context(), cloud)


def _simulated_launch(world: RunsWorld, entered: threading.Event, hold_s: float) -> None:
    """Hold the runs lock like `launch_worker`: record the `launching` run, keep the lock a while."""
    with world.ports.state.locks.runs():
        seed_run(world, "launching")
        entered.set()
        threading.Event().wait(hold_s)


def _launch_in_background(world: RunsWorld, hold_s: float) -> threading.Thread:
    entered = threading.Event()
    launch = threading.Thread(target=_simulated_launch, args=(world, entered, hold_s))
    launch.start()
    assert entered.wait(THREAD_TIMEOUT_S)
    return launch


def test_step_racing_a_launch_keeps_the_manifest_held(runs_world: RunsWorld) -> None:
    """The step waits for the launch's runs lock, then sees its `launching` run: held, not published."""
    seed_uploaded(runs_world)
    launch = _launch_in_background(runs_world, SHORT_HOLD_S)
    step = runs_world.step()
    launch.join(THREAD_TIMEOUT_S)
    assert (step.outcome, [source.kind for source in step.activity], batch_state(runs_world)) == (
        "held",
        ["local_run"],
        HELD,
    )
    assert runs_world.publish_held().held == (FIRST_BATCH_ID,)


def test_step_behind_a_long_launch_gives_runs_busy(runs_world: RunsWorld) -> None:
    """A launch holding the runs lock past the bounded wait: `runs_busy`, nothing changed; re-sending resumes."""
    seed_uploaded(runs_world)
    launch = _launch_in_background(runs_world, LONG_HOLD_S)
    with pytest.raises(RunsBusyError):
        runs_world.step()
    launch.join(THREAD_TIMEOUT_S)
    assert batch_state(runs_world) == UPLOADING
    set_run_status(runs_world, "finished")
    assert runs_world.step().outcome == "published"
