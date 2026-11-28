"""The manifest step publishes without an activity check (A21, architecture §3.9 "Run scope").

A run processes only the batches its lease lists, so a manifest published during a run is safe:
it waits for the next run. Only a pending withdrawal of a leftover manifest takes the runs lock
(and is refused while a live lease lists the batch, `test_manifest_withdrawal_resume.py`). The
`uploading` batch is seeded through the state store, its audio in moto.
"""

import threading

from support.runs import OUTAGE, READY, RUN_ID, UPLOADING, RunsWorld, app_pod_name, batch_state, seed_run, seed_uploaded

# Seconds a test thread waits for an event at most.
THREAD_TIMEOUT_S = 10
# How long the simulated launch holds the runs lock: longer than the test backends' wait (0.5 s).
LONG_HOLD_S = 2.0


def test_published_during_a_run(runs_world: RunsWorld) -> None:
    """A live pod and a local run do not keep the manifest back."""
    seed_uploaded(runs_world)
    runs_world.cloud.api.pods.add(app_pod_name(RUN_ID, runs_world.now()))
    seed_run(runs_world, "running")
    runs_world.step()
    assert batch_state(runs_world) == READY


def test_published_while_runpod_is_unreachable(runs_world: RunsWorld) -> None:
    """The step never reads the Runpod lists: an outage there does not matter."""
    seed_uploaded(runs_world)
    runs_world.cloud.api.fail("GET", "/v2/pods", *OUTAGE)
    runs_world.step()
    assert batch_state(runs_world) == READY


def _simulated_launch(world: RunsWorld, entered: threading.Event, hold_s: float) -> None:
    """Hold the runs lock like `launch_worker`: record the `launching` run, keep the lock a while."""
    with world.ports.state.locks.runs():
        seed_run(world, "launching")
        entered.set()
        threading.Event().wait(hold_s)


def test_step_does_not_wait_for_a_launch(runs_world: RunsWorld) -> None:
    """Without a pending withdrawal the step takes no runs lock: a long launch does not delay it."""
    seed_uploaded(runs_world)
    entered = threading.Event()
    launch = threading.Thread(target=_simulated_launch, args=(runs_world, entered, LONG_HOLD_S))
    launch.start()
    assert entered.wait(THREAD_TIMEOUT_S)
    before = batch_state(runs_world)
    runs_world.step()
    after = batch_state(runs_world)
    launch.join(THREAD_TIMEOUT_S)
    assert (before, after) == (UPLOADING, READY)
