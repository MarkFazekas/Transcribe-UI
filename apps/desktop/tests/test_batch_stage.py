"""`BatchSummary.stage` and `BatchSummary.labels` (plan m6 M6.5): per phase and per flag.

Every seeded scenario of `support/actions.py` (each phase, hidden, audio deleted, a pending
cleanup of either scope, flagged and extracted jobs, retry batches) is listed and its stage and
labels compared with the expected ones; an `uploading` batch is also checked on its facts directly.
"""

import json
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from types import MappingProxyType
from typing import get_args

import pytest
from sqlalchemy import select

from support.actions import (
    ADOPTED,
    COPIED_SOURCE,
    COPYING_RETRY,
    PAST_RETRY,
    RETRIED_SOURCE,
    SCENARIOS,
    scenario_batch_id,
    scenario_item_id,
    seed_scenarios,
    seeded_root,
)
from support.fakes import FIXED_NOW, seeded_ports
from transcribe_ui.api.backend import Backend
from transcribe_ui.batches.actions import BatchFacts, ItemCounts, JobCounts, batch_labels, batch_stage
from transcribe_ui.batches.constants import PHASE_STAGES
from transcribe_ui.state.models import BatchPhase, JobRow, PlacementRow
from transcribe_ui.state.store import LocalState

READY = "subtitles_ready"
FLAGGED = "flagged_jobs"
UNFINISHED = "upload_unfinished"
AUDIO_DELETED = "audio_deleted"
PENDING = "cleanup_pending"
REVIEW_UPLOAD = "review_upload"
TRANSCRIPTION = "transcription"
DOWNLOAD = "download"
DONE = "done"
# A batch's stage and labels.
type Shown = tuple[str, tuple[str, ...]]
# The stage and labels each scenario lists.
EXPECTED = MappingProxyType(
    {
        "composing": ("files", ()),
        "reviewing_open": (REVIEW_UPLOAD, ()),
        "reviewing_done": (REVIEW_UPLOAD, ()),
        "reviewing_single": (REVIEW_UPLOAD, ()),
        "uploading": (REVIEW_UPLOAD, (UNFINISHED,)),
        "uploading_done": (REVIEW_UPLOAD, (UNFINISHED,)),
        "ready": (TRANSCRIPTION, (READY,)),
        "transcribed_flagged": (DOWNLOAD, (READY, FLAGGED)),
        "transcribed_clean": (DOWNLOAD, (READY,)),
        "transcribed_audio_pending": (DOWNLOAD, (PENDING, READY, AUDIO_DELETED)),
        "downloaded_no_speech": (DONE, (FLAGGED,)),
        "downloaded_audio_deleted": (DONE, (FLAGGED, AUDIO_DELETED)),
        "downloaded_hidden": (DONE, ("hidden",)),
        "cleaned": (DONE, (FLAGGED,)),
        "cleaned_pending": (DONE, (PENDING,)),
        COPIED_SOURCE: (DOWNLOAD, (READY, FLAGGED)),
        COPYING_RETRY: (REVIEW_UPLOAD, (UNFINISHED,)),
        RETRIED_SOURCE: (DONE, (FLAGGED, AUDIO_DELETED)),
        PAST_RETRY: (TRANSCRIPTION, ()),
        ADOPTED: (DOWNLOAD, (READY, FLAGGED)),
    },
)


@pytest.fixture
def backend(tmp_path: Path) -> Iterator[Backend]:
    """Return a backend over a database holding every scenario's batch."""
    ports = seeded_ports(tmp_path)
    with closing(ports.state):
        seed_scenarios(ports.state, seeded_root(tmp_path))
        yield Backend(ports)


def _listed(backend: Backend) -> dict[str, Shown]:
    response = backend.execute_json(json.dumps({"action": "list_batches", "include_hidden": True}))
    document = json.loads(response.model_dump_json())
    return {summary["batch_id"]: (summary["stage"], tuple(summary["labels"])) for summary in document["batches"]}


def test_every_scenario_lists_its_stage_and_labels(backend: Backend) -> None:
    """Each phase and flag gives its stage and labels, in the list and in the batch's detail alike."""
    listed = _listed(backend)
    assert set(EXPECTED) == set(SCENARIOS)
    for name, expected in EXPECTED.items():
        batch_id = scenario_batch_id(name)
        assert listed[batch_id] == expected, name
        detail = backend.execute_json(json.dumps({"action": "get_batch", "batch_id": batch_id}))
        summary = json.loads(detail.model_dump_json())["batch"]["summary"]
        assert (summary["stage"], tuple(summary["labels"])) == expected, name


def test_every_phase_has_a_stage() -> None:
    """The stage table covers every phase."""
    assert set(PHASE_STAGES) == set(get_args(BatchPhase.__value__))


def _facts(phase: str) -> BatchFacts:
    return BatchFacts(
        phase=phase,
        audio_deleted=False,
        hidden=False,
        adopted=False,
        cleanup_pending=None,
        item_counts=ItemCounts(total=1),
        job_counts=JobCounts(pending_results=1, flagged=0, extract=0),
        copying_retry=False,
    )


def test_an_upload_is_at_the_review_upload_step() -> None:
    """An `uploading` batch is at the review and upload step until its manifest is published."""
    unfinished = _facts("uploading")
    assert (batch_stage(unfinished), batch_labels(unfinished)) == (REVIEW_UPLOAD, (UNFINISHED,))


def test_the_labels_are_shown_in_order() -> None:
    """Every label that applies at once is listed in the table's order."""
    facts = BatchFacts(
        phase="transcribed",
        audio_deleted=True,
        hidden=True,
        adopted=False,
        cleanup_pending="audio",
        item_counts=ItemCounts(total=2),
        job_counts=JobCounts(pending_results=0, flagged=1, extract=0, subtitles=1),
        copying_retry=False,
    )
    assert batch_labels(facts) == (PENDING, READY, FLAGGED, AUDIO_DELETED, "hidden")


def _place(state: LocalState, name: str, position: int) -> None:
    """Record a placement outcome for the job of a scenario's item."""
    item_id = scenario_item_id(name, position)
    with state.session() as session:
        job = session.scalars(select(JobRow).where(JobRow.item_id == item_id)).one()
        row = PlacementRow(job_id=job.job_id, batch_id=job.batch_id, outcome="place", payload={})
        row.created_at = FIXED_NOW
        session.add(row)


def test_placed_subtitles_are_no_longer_ready(tmp_path: Path) -> None:
    """`subtitles_ready` stays while a subtitle has no placement outcome, and goes once every one has."""
    ports = seeded_ports(tmp_path)
    with closing(ports.state):
        seed_scenarios(ports.state, seeded_root(tmp_path))
        name = "transcribed_clean"
        _place(ports.state, name, 0)
        batch_id = scenario_batch_id(name)
        assert _listed(Backend(ports))[batch_id] == (DOWNLOAD, (READY,))
        _place(ports.state, name, 1)
        assert _listed(Backend(ports))[batch_id] == (DOWNLOAD, ())
        detail = Backend(ports).execute_json(json.dumps({"action": "get_batch", "batch_id": batch_id}))
        assert not json.loads(detail.model_dump_json())["batch"]["summary"]["labels"]
