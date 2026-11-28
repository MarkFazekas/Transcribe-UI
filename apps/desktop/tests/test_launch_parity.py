"""CLI/backend parity and golden files of the launch requests (plan m5 M5.11).

Each leg has its own moto bucket and fake Runpod API (cloud set up through its own backend),
seeded alike; the clock is fixed, so the normalised responses are byte-identical. Regenerate the
golden files with `UPDATE_SNAPSHOTS=1`.
"""

import json
from pathlib import Path

from support.batches import build_batch_tree
from support.credentials import moto_bucket
from support.fakes import FIXED_NOW
from support.golden import assert_golden
from support.launch import (
    CANCEL,
    CANCEL_QUEUED,
    LAUNCH,
    PLAN,
    RECONCILE,
    TERMINATE,
    document,
)
from support.launch_legs import LaunchLegs, seed_held_leg
from support.parity import Exchange, Template, run_parity
from support.runpod import Cloud
from support.runs import OTHER_RUN_ID, RUN_ID, app_pod_name
from transcribe_ui.api.ports import Ports
from transcribe_ui.storage.r2 import R2ObjectStore

# Status of a success response.
OK = "ok"
# Status of a confirmation request.
ASKED = "needs_confirmation"
# Two hours of waiting for a machine.
TWO_HOURS_S = 7200
# Ids the fake gives the first pod and the first queued pod.
FIRST_POD = "pod7919"
FIRST_QUEUED_POD = "qpod7919"


def _field(exchange: Exchange, *path: str) -> str:
    found = json.loads(exchange.response)
    for key in path:
        found = found[key]
    assert isinstance(found, str)
    return found


def _confirmed_launch(exchanges: list[Exchange]) -> str:
    fingerprint = _field(exchanges[0], "plan", "fingerprint")
    return document(LAUNCH, wait_s=TWO_HOURS_S, confirm_fingerprint=fingerprint)


def _cancel_asked(exchanges: list[Exchange]) -> str:
    return document(CANCEL, run_id=_field(exchanges[2], "run", "run_id"))


def _cancel_launched(exchanges: list[Exchange]) -> str:
    return document(CANCEL, run_id=_field(exchanges[2], "run", "run_id"), confirm=True)


def _check(names: list[str], exchanges: list[Exchange]) -> None:
    for name, exchange in zip(names, exchanges, strict=True):
        assert_golden(f"{name}.json", exchange.golden())


def test_launch_flow(tmp_path: Path) -> None:
    """Plan, unconfirmed launch, confirmed launch, reconcile, cancel asked then confirmed, reconcile."""
    templates: list[Template] = [
        document(PLAN),
        document(LAUNCH),
        _confirmed_launch,
        document(RECONCILE),
        _cancel_asked,
        _cancel_launched,
        document(RECONCILE),
    ]
    legs = LaunchLegs(seed=seed_held_leg)
    with moto_bucket():
        exchanges = run_parity(tmp_path, templates, prepare=build_batch_tree, ports=legs)
    statuses = [json.loads(exchange.response)["status"] for exchange in exchanges]
    assert statuses == [OK, ASKED, OK, OK, ASKED, OK, OK]
    names = ["plan", "unconfirmed", "launched", "reconciled", "cancel_asked", "cancelled", "reconciled_after"]
    _check([f"launch_flow_{index:02d}_{name}" for index, name in enumerate(names, start=1)], exchanges)


def _seed_stragglers(_ports: Ports, _root: Path, _store: R2ObjectStore, cloud: Cloud) -> None:
    """A pod and a queued pod of runs this database does not know."""
    cloud.api.pods.add(app_pod_name(RUN_ID, FIXED_NOW))
    cloud.api.queued_pods.add(app_pod_name(OTHER_RUN_ID, FIXED_NOW))


def test_stragglers(tmp_path: Path) -> None:
    """Reconcile reports stragglers; terminate and cancel them, each confirmed first."""
    templates: list[Template] = [
        document(RECONCILE),
        document(TERMINATE, pod_id=FIRST_POD),
        document(TERMINATE, pod_id=FIRST_POD, confirm=True),
        document(CANCEL_QUEUED, queued_pod_id=FIRST_QUEUED_POD, confirm=True),
        document(RECONCILE),
    ]
    legs = LaunchLegs(seed=_seed_stragglers)
    with moto_bucket():
        exchanges = run_parity(tmp_path, templates, prepare=build_batch_tree, ports=legs)
    statuses = [json.loads(exchange.response)["status"] for exchange in exchanges]
    assert statuses == [OK, ASKED, OK, OK, OK]
    names = ["reconciled", "terminate_asked", "terminated", "queued_cancelled", "reconciled_after"]
    _check([f"stragglers_{index:02d}_{name}" for index, name in enumerate(names, start=1)], exchanges)
