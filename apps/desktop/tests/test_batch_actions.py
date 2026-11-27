"""`BatchSummary.actions` (plan m6 M6.1): the table per phase and state, and its drift test against the guards.

The drift test sends every action to a batch of every seeded scenario, each action on a database
of its own (some requests change their batch). A listed action must pass its handler's local
guard (it may still fail later, on missing credentials, a file that is not there, a confirmation
it asks for); an unlisted one must be refused by that guard.
"""

import json
from collections.abc import Iterator
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from types import MappingProxyType
from typing import get_args

import pytest

from support.actions import (
    COPIED_SOURCE,
    LANGUAGE,
    SCENARIOS,
    SEEDED_ROOT,
    retry_job,
    scenario_batch_id,
    scenario_item_id,
    seed_scenarios,
)
from support.fakes import FIXED_NOW, seeded_ports
from transcribe_contracts.classes import JobRef, RunStatus
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.ports import Ports
from transcribe_ui.batches.constants import ACTION_RULES
from transcribe_ui.batches.responses import BatchAction
from transcribe_ui.runs.worker import WorkerReport, worker_progress

# Error codes of the batch guards the table restates (everything else comes after the guard).
GUARD_CODES = frozenset(
    (
        "wrong_phase",
        "last_item",
        "review_incomplete",
        "audio_deleted",
        "retry_job_invalid",
        "results_pending",
        "retry_copying",
    ),
)
# Every action a summary can list.
EVERY_ACTION: tuple[str, ...] = get_args(BatchAction.__value__)
# Actions named once here, listed by many scenarios.
SHOW = "show_batch"
HIDE = "hide_batch"
CLEAN_AUDIO = "cleanup_audio"
CLEAN_ALL = "cleanup_everything"
RETRY = "create_retry_batch"
UPLOAD = "run_upload"
EXCLUDE = "exclude_items"
# The downloads, allowed together from `ready` on.
DOWNLOADS = ("plan_download", "run_download", "export_custom_plan", "import_custom_plan")
# Review and choices, allowed together in `reviewing` and `uploading`.
REVIEW = ("review_batch", "resolve_review")
# Before the upload: continue to review, or delete.
BEFORE_UPLOAD = ("start_review", "delete_batch")
# The actions each scenario lists, in the table's order.
EXPECTED = MappingProxyType(
    {
        "composing": ("edit_batch", *BEFORE_UPLOAD, SHOW),
        "reviewing_open": (*BEFORE_UPLOAD, *REVIEW, EXCLUDE, SHOW),
        "reviewing_done": (*BEFORE_UPLOAD, *REVIEW, EXCLUDE, UPLOAD, SHOW),
        "reviewing_single": (*BEFORE_UPLOAD, *REVIEW, UPLOAD, SHOW),
        "uploading": (*REVIEW, EXCLUDE, UPLOAD, CLEAN_ALL, SHOW),
        "ready": (*DOWNLOADS, CLEAN_ALL, SHOW),
        "transcribed_flagged": (*DOWNLOADS, RETRY, CLEAN_AUDIO, CLEAN_ALL, SHOW),
        "transcribed_clean": (*DOWNLOADS, CLEAN_AUDIO, CLEAN_ALL, SHOW),
        "downloaded_no_speech": (*DOWNLOADS, RETRY, CLEAN_AUDIO, CLEAN_ALL, HIDE, SHOW),
        "downloaded_audio_deleted": (*DOWNLOADS, CLEAN_AUDIO, CLEAN_ALL, HIDE, SHOW),
        "cleaned": (*DOWNLOADS, CLEAN_ALL, HIDE, SHOW),
        COPIED_SOURCE: (*DOWNLOADS, RETRY, SHOW),
        "copying_retry": (*REVIEW, UPLOAD, CLEAN_ALL, SHOW),
    },
)
# The request action of an allowed action that is split by scope or direction.
REQUEST_ACTIONS = MappingProxyType(
    {CLEAN_AUDIO: "cleanup_cloud", CLEAN_ALL: "cleanup_cloud", HIDE: "set_batch_hidden", SHOW: "set_batch_hidden"},
)
# A run id of the worker report test.
RUN_ID = "01JDQ0000000000000000000R1"


@pytest.fixture
def ports(tmp_path: Path) -> Iterator[Ports]:
    """Return test ports over a database holding every scenario's batch (closed afterwards)."""
    created = seeded_ports(tmp_path)
    with closing(created.state):
        seed_scenarios(created.state)
        yield created


@pytest.fixture
def backend(ports: Ports) -> Backend:
    """Return a backend on the seeded ports."""
    return Backend(ports)


def _listed(backend: Backend) -> dict[str, tuple[str, ...]]:
    response = backend.execute_json(json.dumps({"action": "list_batches", "include_hidden": True}))
    assert response.status == "ok", response
    document = json.loads(response.model_dump_json())
    return {summary["batch_id"]: tuple(summary["actions"]) for summary in document["batches"]}


def _detail_actions(backend: Backend, batch_id: str) -> tuple[str, ...]:
    response = backend.execute_json(json.dumps({"action": "get_batch", "batch_id": batch_id}))
    document = json.loads(response.model_dump_json())
    return tuple(document["batch"]["summary"]["actions"])


def _fields(action: str, name: str) -> dict[str, object]:
    """Return the request fields besides `action` and `batch_id` that reach the action's guard."""
    first_item = scenario_item_id(name, 0)
    operations = [{"operation": "include", "path": SEEDED_ROOT, "filters": {}}]
    plan = {"plan_path": f"{SEEDED_ROOT}/plan.json", "placement": {"output_folder": SEEDED_ROOT}}
    shapes: dict[str, dict[str, object]] = {
        "edit_batch": {"scan": {"root": SEEDED_ROOT}, "operations": operations, "languages": [LANGUAGE]},
        "resolve_review": {"choices": [{"kind": "transcribe", "item_id": first_item, "language": LANGUAGE}]},
        EXCLUDE: {"item_ids": [first_item]},
        "import_custom_plan": {**plan, "check_only": True},
        RETRY: {"jobs": [retry_job(name)]},
        CLEAN_AUDIO: {"scope": "audio"},
        CLEAN_ALL: {"scope": "everything"},
        HIDE: {"hidden": True},
        SHOW: {"hidden": False},
    }
    return shapes.get(action, {})


def _request(action: str, name: str) -> str:
    request_action = REQUEST_ACTIONS.get(action, action)
    return json.dumps({"action": request_action, "batch_id": scenario_batch_id(name), **_fields(action, name)})


def test_table_lists_every_action_once() -> None:
    """The table and the `BatchAction` literal name the same actions in the same order."""
    assert tuple(ACTION_RULES) == EVERY_ACTION


def test_actions_per_phase_and_state(backend: Backend) -> None:
    """Every seeded batch lists the actions its phase, flags, items and jobs allow; `get_batch` agrees."""
    listed = _listed(backend)
    for name, expected in EXPECTED.items():
        batch_id = scenario_batch_id(name)
        assert (listed[batch_id], _detail_actions(backend, batch_id)) == (expected, expected), name


@pytest.mark.parametrize("action", EVERY_ACTION)
def test_listed_actions_pass_their_guard(backend: Backend, action: str) -> None:
    """Drift test: for every scenario, the action is listed exactly when its handler's guard lets it through."""
    listed = _listed(backend)
    for name in SCENARIOS:
        response = backend.execute_json(_request(action, name))
        refused = response.status == "error" and response.error.code in GUARD_CODES
        allowed = action in listed[scenario_batch_id(name)]
        assert allowed != refused, (name, action, response)


def _current_name(ports: Ports, current: JobRef) -> str | None:
    status = RunStatus(
        run_id=RUN_ID,
        pod_id="pod1",
        state="transcribing",
        started_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        deadline_at=FIXED_NOW + timedelta(hours=2),
        current=current,
        done_jobs=0,
        total_jobs=2,
    )
    progress = worker_progress(ports.state, WorkerReport(status))
    assert progress.current is not None
    return progress.current.name


def test_current_job_is_named_by_its_file(ports: Ports) -> None:
    """The worker's current job gets its item's file name; a batch or item this database does not know gets none."""
    known = JobRef(batch_id=scenario_batch_id("ready"), item_id=scenario_item_id("ready", 1), language=LANGUAGE)
    other_batch = known.model_copy(update={"batch_id": scenario_batch_id("uploading")})
    unknown = known.model_copy(update={"batch_id": "batch_20260101_090000"})
    names = [_current_name(ports, current) for current in (known, other_batch, unknown)]
    assert names == ["file1.mp3", None, None]
