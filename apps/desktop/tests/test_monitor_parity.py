"""CLI/backend parity and golden files of `refresh_status` (plan m5 M5.12).

Each leg has its own moto bucket and fake Runpod API, seeded alike; between requests both legs'
clocks move and their worker objects are written the same way, so the normalised responses are
byte-identical. Regenerate the golden files with `UPDATE_SNAPSHOTS=1`.
"""

import json
from collections.abc import Callable
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Any

from support.batches import FIRST_BATCH_ID, build_batch_tree
from support.credentials import moto_bucket
from support.golden import assert_golden
from support.launch import LAUNCH, PLAN, document
from support.launch_legs import LaunchLegs, seed_held_leg
from support.monitor import LIST_RUNS, REFRESH, write_done, write_log, write_result, write_status
from support.parity import Exchange, Template, run_parity
from support.runpod import Cloud
from transcribe_contracts.classes import JobRef
from transcribe_ui.api.ports import Ports
from transcribe_ui.storage.r2 import R2ObjectStore

# Two hours of waiting for a machine.
TWO_HOURS_S = 7200
# How long the clock moves on before a run list (elapsed time and cost must not follow it).
LATER_S = 600
# How long the simulated worker transcribes before the next refresh.
TRANSCRIBING_S = 1200
# Item ids of the seeded batch.
FILM_ID = "01JDZZZZZZZZZZZZZZZZZZZZ01"
PODCAST_ID = "01JDZZZZZZZZZZZZZZZZZZZZ02"
# Changes one leg's cloud and bucket before a request (the run id given).
type LegChange = Callable[[Cloud, R2ObjectStore, str], None]


def _seed_queued(ports: Ports, root: Path, store: R2ObjectStore, cloud: Cloud) -> None:
    """The held batch and another computer's manifest; no capacity, so the launch queues."""
    seed_held_leg(ports, root, store, cloud)
    cloud.settings.capacity = False


def _run_id(exchanges: list[Exchange]) -> str:
    run_id = json.loads(exchanges[1].response)["run"]["run_id"]
    assert isinstance(run_id, str)
    return run_id


def _launch(exchanges: list[Exchange]) -> str:
    fingerprint = json.loads(exchanges[0].response)["plan"]["fingerprint"]
    return document(LAUNCH, wait_s=TWO_HOURS_S, confirm_fingerprint=fingerprint)


def _refresh_after(legs: LaunchLegs, change: LegChange) -> Template:
    """Return a template that applies `change` to both legs, then refreshes."""
    return partial(_changed_refresh, legs, change)


def _changed_refresh(legs: LaunchLegs, change: LegChange, exchanges: list[Exchange]) -> str:
    for cloud, store in zip(legs.clouds, legs.stores, strict=True):
        change(cloud, store, _run_id(exchanges))
    return document(REFRESH)


def _deploy(cloud: Cloud, _store: R2ObjectStore, _run: str) -> None:
    queued = cloud.api.queued_pods.listed()[0]["id"]
    cloud.api.queued_pods.schedule_deploy(str(queued), cloud.clock.now())
    cloud.clock.advance(timedelta(minutes=1))


def _transcribe(cloud: Cloud, store: R2ObjectStore, run_id: str) -> None:
    current = JobRef(batch_id=FIRST_BATCH_ID, item_id=FILM_ID, language="en")
    write_status(store, run_id, "transcribing", cloud.clock.now(), current=current)
    cloud.clock.advance(timedelta(seconds=TRANSCRIBING_S))


def _finish(cloud: Cloud, store: R2ObjectStore, run_id: str) -> None:
    now = cloud.clock.now()
    write_result(store, FIRST_BATCH_ID, FILM_ID, run_id, now)
    write_result(store, FIRST_BATCH_ID, PODCAST_ID, run_id, now, status="failed")
    write_done(store, FIRST_BATCH_ID, run_id, now)
    write_status(store, run_id, "finished", now, done_jobs=2)
    write_log(store, run_id)
    pod_id = str(cloud.api.pods.listed()[0]["id"])
    cloud.api.pods.records.pop(pod_id)
    cloud.clock.advance(timedelta(minutes=1))


def _advance(cloud: Cloud, _store: R2ObjectStore, _run: str) -> None:
    cloud.clock.advance(timedelta(seconds=LATER_S))


def _list_after(legs: LaunchLegs, change: LegChange) -> Template:
    """Return a template that applies `change` to both legs, then lists the runs."""
    return partial(_changed_list, legs, change)


def _changed_list(legs: LaunchLegs, change: LegChange, exchanges: list[Exchange]) -> str:
    for cloud, store in zip(legs.clouds, legs.stores, strict=True):
        change(cloud, store, _run_id(exchanges))
    return document(LIST_RUNS)


def _only_run(exchange: Exchange) -> dict[str, Any]:
    runs = json.loads(exchange.response)["runs"]
    assert len(runs) == 1
    run = runs[0]
    assert isinstance(run, dict)
    return run


# The steps of the monitoring flow, by golden name.
FLOW_NAMES = (
    "plan",
    "launched",
    "listed_launched",
    "waiting",
    "starting",
    "transcribing",
    "listed_transcribing",
    "finished",
    "nothing_open",
    "listed_finished",
)


def _flow(tmp_path: Path) -> dict[str, Exchange]:
    """Plan, launch (queued), then refreshes and run lists through both legs; return each step by name."""
    legs = LaunchLegs(seed=_seed_queued)
    templates: list[Template] = [
        document(PLAN),
        _launch,
        document(LIST_RUNS),
        document(REFRESH),
        _refresh_after(legs, _deploy),
        _refresh_after(legs, _transcribe),
        _list_after(legs, _advance),
        _refresh_after(legs, _finish),
        document(REFRESH),
        _list_after(legs, _advance),
    ]
    with moto_bucket():
        exchanges = run_parity(tmp_path, templates, prepare=build_batch_tree, ports=legs)
    return dict(zip(FLOW_NAMES, exchanges, strict=True))


def test_monitor_flow(tmp_path: Path) -> None:
    """Plan, launch (queued), then refreshes and run lists: waiting, starting, transcribing, finished.

    `list_runs` shows what the last refresh stored: elapsed time and cost do not move with the
    clock between refreshes, and the worker report names the current file.
    """
    flow = _flow(tmp_path)
    assert {json.loads(exchange.response)["status"] for exchange in flow.values()} == {"ok"}
    for index, name in enumerate(FLOW_NAMES, start=1):
        assert_golden(f"monitor_flow_{index:02d}_{name}.json", flow[name].golden())
    launched = _only_run(flow["listed_launched"])
    assert (launched["stage"], launched["refreshed_at"], launched["elapsed_s"]) == ("waiting", None, None)
    listed = _only_run(flow["listed_transcribing"])
    assert listed == json.loads(flow["transcribing"].response)["runs"][0]
    assert listed["worker"]["current"]["name"] == "Film.mkv"
    finished = _only_run(flow["listed_finished"])
    assert (finished["stage"], finished["elapsed_until"]) == ("finished", finished["ended_at"])
