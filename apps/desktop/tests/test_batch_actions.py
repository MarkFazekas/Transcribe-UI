"""`BatchSummary.actions` (plan m6 M6.1): the table per phase and state, and its drift test against the guards.

The drift test sends every action to a batch of every seeded scenario (each action on a database
of its own, since some requests change their batch) and checks each outcome against a closed
list: a listed action, and an unlisted one the table leaves out only because it would change
nothing (`NO_OPS`), must give one of the outcomes expected once its handler's guard passed
(`AFTER_GUARD`: a success, a confirmation, missing credentials, a file that is not there, ...);
any other unlisted action must be refused by a guard (`GUARD_CODES`). A new guard in a handler,
or a table entry that lets through what the guard refuses, gives an outcome outside these lists.
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
    ADOPTED,
    COPIED_SOURCE,
    COPYING_RETRY,
    LANGUAGE,
    PAST_RETRY,
    REMOVED,
    RETRIED_SOURCE,
    SCENARIOS,
    retry_job,
    scenario_batch_id,
    scenario_item_id,
    seed_scenarios,
    seeded_root,
)
from support.fakes import FIXED_NOW, seeded_ports
from transcribe_contracts.classes import JobRef, RunStatus
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import Response
from transcribe_ui.api.ports import Ports
from transcribe_ui.batches.constants import ACTION_RULES
from transcribe_ui.batches.responses import BatchAction
from transcribe_ui.runs.worker import WorkerReport, stored_report

# Error codes of the batch guards the table restates.
GUARD_CODES = frozenset(
    (
        "wrong_phase",
        "last_item",
        "review_incomplete",
        "audio_deleted",
        "retry_job_invalid",
        "results_pending",
        "retry_copying",
        "adopted_batch",
        "batch_removed_from_cloud",
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
START = "start_review"
DELETE = "delete_batch"
OK = "ok"
ASKS = "needs_confirmation"
# The downloads, allowed together from `ready` on.
DOWNLOADS = ("plan_download", "run_download", "export_custom_plan", "import_custom_plan")
# The no-op of every shown batch: showing it.
SHOWN = (SHOW,)
# Review and choices, allowed together while an item is in review.
REVIEW = ("review_batch", "resolve_review")
# What each action may answer once its guard let it through (the seeded files do not exist, no
# credentials are stored, confirmations are not sent, no audio object of a retry source is recorded,
# the default placement is next to the media).
AFTER_GUARD = MappingProxyType(
    {
        "edit_batch": frozenset(("root_not_found",)),
        START: frozenset((OK,)),
        DELETE: frozenset((ASKS,)),
        "review_batch": frozenset((OK,)),
        "resolve_review": frozenset(("choice_invalid", OK)),
        EXCLUDE: frozenset((ASKS,)),
        UPLOAD: frozenset(("credentials_missing",)),
        "plan_download": frozenset((OK,)),
        # The default strategy (next to the media) is refused for another computer's job: the request's own field.
        "run_download": frozenset((OK, "credentials_missing", "download_plan_invalid")),
        "export_custom_plan": frozenset((OK,)),
        "import_custom_plan": frozenset(("custom_plan_unreadable",)),
        RETRY: frozenset(("credentials_missing", "copy_source_missing", OK)),
        CLEAN_AUDIO: frozenset((ASKS,)),
        CLEAN_ALL: frozenset((ASKS,)),
        HIDE: frozenset((OK,)),
        SHOW: frozenset((OK,)),
    },
)
# The actions each scenario lists, in the table's order.
EXPECTED = MappingProxyType(
    {
        "composing": ("edit_batch", START, DELETE),
        "reviewing_open": (DELETE, *REVIEW, EXCLUDE),
        "reviewing_done": (DELETE, *REVIEW, EXCLUDE, UPLOAD),
        "reviewing_single": (DELETE, *REVIEW, UPLOAD),
        "uploading": (*REVIEW, EXCLUDE, UPLOAD, CLEAN_ALL),
        "uploading_done": (EXCLUDE, UPLOAD, CLEAN_ALL),
        "ready": (*DOWNLOADS, CLEAN_ALL),
        "transcribed_flagged": (*DOWNLOADS, RETRY, CLEAN_AUDIO, CLEAN_ALL),
        "transcribed_clean": (*DOWNLOADS, CLEAN_AUDIO, CLEAN_ALL),
        "transcribed_audio_pending": (*DOWNLOADS, CLEAN_AUDIO, CLEAN_ALL),
        "downloaded_no_speech": (*DOWNLOADS, RETRY, CLEAN_AUDIO, CLEAN_ALL, HIDE),
        "downloaded_audio_deleted": (*DOWNLOADS, CLEAN_ALL, HIDE),
        "downloaded_hidden": (*DOWNLOADS, CLEAN_AUDIO, CLEAN_ALL, SHOW),
        "cleaned": (HIDE,),
        "cleaned_pending": (*DOWNLOADS, CLEAN_ALL, HIDE),
        COPIED_SOURCE: (*DOWNLOADS, RETRY),
        COPYING_RETRY: (*REVIEW, UPLOAD, CLEAN_ALL),
        RETRIED_SOURCE: (*DOWNLOADS, CLEAN_ALL, HIDE),
        PAST_RETRY: (*DOWNLOADS, CLEAN_ALL),
        ADOPTED: (*DOWNLOADS, CLEAN_ALL),
        REMOVED: (HIDE,),
    },
)
# Unlisted actions whose guard lets them through although they would change nothing: showing a
# batch that is shown (and hiding a hidden one), starting a review that started, reviewing with
# nothing in review, deleting audio or cloud data already gone, downloading from a cleaned batch
# without extracted subtitles, and a retry whose job set already has its retry batch (the
# request returns that batch; with the source's audio deleted it is no new retry).
NO_OPS = MappingProxyType(
    {
        "composing": SHOWN,
        "reviewing_open": (START, SHOW),
        "reviewing_done": (START, SHOW),
        "reviewing_single": (START, SHOW),
        "uploading": SHOWN,
        "uploading_done": (*REVIEW, SHOW),
        "ready": SHOWN,
        "transcribed_flagged": SHOWN,
        "transcribed_clean": SHOWN,
        "transcribed_audio_pending": SHOWN,
        "downloaded_no_speech": SHOWN,
        "downloaded_audio_deleted": (CLEAN_AUDIO, SHOW),
        "downloaded_hidden": (HIDE,),
        "cleaned": (*DOWNLOADS, CLEAN_ALL, SHOW),
        "cleaned_pending": SHOWN,
        COPIED_SOURCE: SHOWN,
        COPYING_RETRY: SHOWN,
        RETRIED_SOURCE: (RETRY, CLEAN_AUDIO, SHOW),
        PAST_RETRY: SHOWN,
        ADOPTED: SHOWN,
        REMOVED: (*DOWNLOADS, SHOW),
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
        seed_scenarios(created.state, seeded_root(tmp_path))
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


def _fields(action: str, name: str, root: Path) -> dict[str, object]:
    """Return the request fields besides `action` and `batch_id` that reach the action's guard.

    Paths are below the seeded scan root, absolute on every OS (they do not exist).
    """
    first_item = scenario_item_id(name, 0)
    folder = str(root)
    operations = [{"operation": "include", "path": folder, "filters": {}}]
    plan = {"plan_path": str(root / "plan.json"), "placement": {"output_folder": folder}}
    shapes: dict[str, dict[str, object]] = {
        "edit_batch": {"scan": {"root": folder}, "operations": operations, "languages": [LANGUAGE]},
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


def _request(action: str, name: str, root: Path) -> str:
    request_action = REQUEST_ACTIONS.get(action, action)
    fields = _fields(action, name, root)
    return json.dumps({"action": request_action, "batch_id": scenario_batch_id(name), **fields})


def test_table_lists_every_action_once() -> None:
    """The table and the `BatchAction` literal name the same actions in the same order."""
    assert tuple(ACTION_RULES) == EVERY_ACTION


def test_actions_per_phase_and_state(backend: Backend) -> None:
    """Every seeded batch lists the actions its phase, flags, items and jobs allow; `get_batch` agrees."""
    listed = _listed(backend)
    for name, expected in EXPECTED.items():
        batch_id = scenario_batch_id(name)
        assert (listed[batch_id], _detail_actions(backend, batch_id)) == (expected, expected), name


def _outcome(response: Response) -> str:
    return response.error.code if response.status == "error" else response.status


def _expected_outcomes(action: str, name: str, listed: tuple[str, ...]) -> frozenset[str]:
    """Return the outcomes the action may have on the scenario's batch."""
    if action in listed or action in NO_OPS.get(name, ()):
        return AFTER_GUARD[action]
    return GUARD_CODES


def test_no_ops_are_not_listed() -> None:
    """An action the table leaves out as a no-op is not also expected in the list."""
    for name, no_ops in NO_OPS.items():
        assert not set(no_ops) & set(EXPECTED[name]), name


@pytest.mark.parametrize("action", EVERY_ACTION)
def test_listed_actions_pass_their_guard(backend: Backend, action: str, tmp_path: Path) -> None:
    """Drift test: every outcome is one the action's listing (or no-op) state allows."""
    listed = _listed(backend)
    for name in SCENARIOS:
        response = backend.execute_json(_request(action, name, seeded_root(tmp_path)))
        expected = _expected_outcomes(action, name, listed[scenario_batch_id(name)])
        assert _outcome(response) in expected, (name, action, response)


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
    stored = stored_report(ports.state, WorkerReport(status))
    assert stored.current is not None
    return stored.current.name


def test_current_job_is_named_by_its_file(ports: Ports) -> None:
    """The worker's current job gets its item's file name; a batch or item this database does not know gets none."""
    known = JobRef(batch_id=scenario_batch_id("ready"), item_id=scenario_item_id("ready", 1), language=LANGUAGE)
    other_batch = known.model_copy(update={"batch_id": scenario_batch_id("uploading")})
    unknown = known.model_copy(update={"batch_id": "batch_20260101_090000"})
    names = [_current_name(ports, current) for current in (known, other_batch, unknown)]
    assert names == ["file1.mp3", None, None]
