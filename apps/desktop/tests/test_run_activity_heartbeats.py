"""The run-activity check from worker heartbeats (plan m5 M5.9): fresh, stale, final, unreadable, far ahead."""

import json
from datetime import timedelta

from support.runs import JSON_TYPE, OTHER_RUN_ID, RUN_ID, RunsWorld, seed_heartbeat
from transcribe_contracts.classes import RunState
from transcribe_contracts.layout import run_log_key, run_status_key
from transcribe_ui.runs.constants import HEARTBEAT_MARGIN_S, HEARTBEAT_STALE_S

# How long a heartbeat counts after its `updated_at`.
STALE = timedelta(seconds=HEARTBEAT_STALE_S)
# Long enough after its last write that a heartbeat object never counts (a minute of slack: the
# test clock starts at the real time, before moto stamps the object).
LONG_AFTER = timedelta(seconds=HEARTBEAT_STALE_S + HEARTBEAT_MARGIN_S + 60)
# A worker clock far ahead of this computer's.
FAR_AHEAD = timedelta(weeks=4)
# The worker state of a run in the middle of its jobs.
TRANSCRIBING: RunState = "transcribing"
# The heartbeat key of the main test run.
KEY = run_status_key(RUN_ID)


def _sources(world: RunsWorld) -> list[tuple[str, str, str | None]]:
    return [(source.kind, source.status, source.run_id) for source in world.activity().sources]


def test_a_fresh_heartbeat_is_active(runs_world: RunsWorld) -> None:
    """A worker status that is not final and recent counts, even with no run known anywhere else."""
    seed_heartbeat(runs_world, TRANSCRIBING, runs_world.now() - STALE)
    sources = [(source.kind, source.name, source.status, source.run_id) for source in runs_world.activity().sources]
    assert sources == [("heartbeat", KEY, TRANSCRIBING, RUN_ID)]


def test_a_heartbeat_ahead_of_the_clock_is_active(runs_world: RunsWorld) -> None:
    """A worker clock ahead of this one does not make a live heartbeat look stale."""
    seed_heartbeat(runs_world, "starting", runs_world.now() + timedelta(minutes=5))
    assert _sources(runs_world) == [("heartbeat", "starting", RUN_ID)]


def test_stale_and_final_heartbeats_are_not_active(runs_world: RunsWorld) -> None:
    """A heartbeat older than the stale limit (a crashed worker) or with a final state does not count."""
    seed_heartbeat(runs_world, TRANSCRIBING, runs_world.now() - STALE - timedelta(seconds=1))
    seed_heartbeat(runs_world, "finished", runs_world.now(), run_id=OTHER_RUN_ID)
    assert not runs_world.activity().sources


def test_unreadable_heartbeat_counts_while_fresh(runs_world: RunsWorld) -> None:
    """A fresh `status.json` that cannot be read may be a live worker's (fail closed); an old one is not."""
    runs_world.store.write(KEY, b"{not json", JSON_TYPE)
    runs_world.store.write(run_log_key(RUN_ID), b"log lines", "text/plain")
    assert _sources(runs_world) == [("heartbeat", "unreadable", RUN_ID)]
    runs_world.cloud.clock.advance(LONG_AFTER)
    assert not runs_world.activity().sources


def test_newer_major_heartbeat_counts_while_fresh(runs_world: RunsWorld) -> None:
    """A heartbeat of a newer contract major cannot be read: it counts while fresh, not once old."""
    key = seed_heartbeat(runs_world, TRANSCRIBING, runs_world.now())
    body = runs_world.store.read(key)
    assert body is not None
    newer = json.loads(body) | {"schema_version": "2.0"}
    runs_world.store.write(key, json.dumps(newer).encode(), JSON_TYPE)
    assert _sources(runs_world) == [("heartbeat", "unreadable", RUN_ID)]
    runs_world.cloud.clock.advance(LONG_AFTER)
    assert not runs_world.activity().sources


def test_far_future_heartbeat_counts_while_fresh(runs_world: RunsWorld) -> None:
    """An `updated_at` far ahead counts while the object is fresh, then no longer."""
    future = runs_world.now() + FAR_AHEAD
    seed_heartbeat(runs_world, TRANSCRIBING, future, deadline_at=future + timedelta(hours=1))
    assert _sources(runs_world) == [("heartbeat", TRANSCRIBING, RUN_ID)]
    runs_world.cloud.clock.advance(LONG_AFTER)
    assert not runs_world.activity().sources


def test_a_future_heartbeat_is_capped_at_its_deadline(runs_world: RunsWorld) -> None:
    """An `updated_at` ahead of the clock counts at most until the run's deadline plus the margin."""
    deadline = runs_world.now() - STALE * 2
    seed_heartbeat(runs_world, TRANSCRIBING, runs_world.now() + FAR_AHEAD, deadline_at=deadline)
    assert not runs_world.activity().sources
