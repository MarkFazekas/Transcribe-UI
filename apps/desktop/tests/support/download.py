"""Seeded batches and helpers of the download tests (plan m5 M5.13).

A batch is stored through the state store in the phase a test needs, with its jobs' results as
`refresh_status` records them; the subtitles of its `ok` jobs are written into the bucket where
the worker writes them. Nothing runs a worker.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from support.batches import EPISODE, FILM, FIRST_BATCH_ID, PODCAST, SILENT, media_dir
from support.fakes import FIXED_NOW
from support.monitor import SUBTITLE
from support.parity import Leg
from transcribe_contracts.layout import result_srt_key
from transcribe_ui.api.classes import Response
from transcribe_ui.batches.payloads import BatchPayload, ItemPayload, JobPayload
from transcribe_ui.discovery.rules import host_rules
from transcribe_ui.media.fake import FakeMediaTools
from transcribe_ui.state.models import BatchRow, ExtractedSubtitleRow, ItemRow, JobRow, PlacementRow
from transcribe_ui.state.store import LocalState
from transcribe_ui.storage.r2 import R2ObjectStore
from transcribe_ui.upload.payloads import ExtractedSubtitlePayload

# A response document as a dict.
type Document = dict[str, Any]

# Item ids of the seeded batch.
FILM_ID = "01JDZZZZZZZZZZZZZZZZZZZZ01"
PODCAST_ID = "01JDZZZZZZZZZZZZZZZZZZZZ02"
EPISODE_ID = "01JDZZZZZZZZZZZZZZZZZZZZ03"
SILENT_ID = "01JDZZZZZZZZZZZZZZZZZZZZ04"
# The SRT extracted from the silent film's embedded Hungarian subtitle.
EXTRACTED_SRT = "1\n00:00:00,000 --> 00:00:02,000\nSzia\n"
# Languages of the seeded jobs.
ENGLISH = "en"
HUNGARIAN = "hu"
# Phase of a batch whose every job has a placement outcome.
DOWNLOADED = "downloaded"
# Failure reason of the failed job.
FAILURE = "decoding failed"
# Output folder of the mirror and flat strategies, below each leg's root.
OUTPUT = "<root>/out"
# Request actions.
PLAN_DOWNLOAD = "plan_download"
RUN_DOWNLOAD = "run_download"
EXPORT_PLAN = "export_custom_plan"
IMPORT_PLAN = "import_custom_plan"


@dataclass(frozen=True)
class SeedJob:
    """One item of a seeded batch with its single job."""

    item_id: str
    name: str
    folder: str
    language: str
    kind: str
    status: str
    result_error: str | None = None


# The film transcribed, the podcast without speech, the episode failed, the silent film's
# subtitle extracted. The film already has an English sidecar subtitle (`Film.en.srt`).
TRANSCRIBED_JOBS = (
    SeedJob(FILM_ID, FILM, "Movies", ENGLISH, "transcribe", "ok"),
    SeedJob(PODCAST_ID, PODCAST, "Audio", ENGLISH, "transcribe", "no_speech"),
    SeedJob(EPISODE_ID, EPISODE, "Audio", ENGLISH, "transcribe", "failed", FAILURE),
    SeedJob(SILENT_ID, SILENT, "Movies", HUNGARIAN, "extract", "extracted"),
)
# Two transcribe jobs without results yet (a `ready` batch).
PENDING_JOBS = (
    SeedJob(FILM_ID, FILM, "Movies", ENGLISH, "transcribe", "pending"),
    SeedJob(PODCAST_ID, PODCAST, "Audio", ENGLISH, "transcribe", "pending"),
)


def seed_download_batch(
    state: LocalState,
    root: Path,
    phase: str,
    jobs: Sequence[SeedJob] = TRANSCRIBED_JOBS,
) -> None:
    """Store the first batch in `phase` with one job per seeded item, in the given order."""
    media = media_dir(root)
    payload = BatchPayload(scan_root=str(media), path_flavor=host_rules().flavor, languages=("en",))
    with state.session() as session:
        session.add(
            BatchRow(
                batch_id=FIRST_BATCH_ID,
                phase=phase,
                manifest_held=False,
                audio_deleted=False,
                hidden=False,
                created_at=FIXED_NOW,
                updated_at=FIXED_NOW,
                payload=payload.model_dump(mode="json"),
            ),
        )
        session.flush()
        for position, seeded in enumerate(jobs):
            path = media / seeded.folder / seeded.name
            stored = ItemPayload(
                position=position,
                path=str(path),
                relative_path=f"{seeded.folder}/{seeded.name}",
                name=seeded.name,
                kind="audio" if seeded.name == PODCAST else "video",
                size_bytes=0,
                mtime_ns=path.stat().st_mtime_ns,
                subtitle_languages=(),
                probe=FakeMediaTools().default_probe,
            )
            session.add(
                ItemRow(
                    item_id=seeded.item_id,
                    batch_id=FIRST_BATCH_ID,
                    phase="uploaded",
                    payload=stored.model_dump(mode="json"),
                ),
            )
            session.flush()
            _seed_job(session, seeded)


def _seed_job(session: Session, seeded: SeedJob) -> None:
    extract = seeded.kind == "extract"
    job = JobPayload(mode="normal", stream_index=0 if extract else None, result_error=seeded.result_error)
    row = JobRow(
        item_id=seeded.item_id,
        batch_id=FIRST_BATCH_ID,
        language=seeded.language,
        kind=seeded.kind,
        status=seeded.status,
        payload=job.model_dump(mode="json"),
    )
    session.add(row)
    session.flush()
    if extract:
        extracted = ExtractedSubtitlePayload(stream_index=0).model_dump(mode="json")
        session.add(ExtractedSubtitleRow(job_id=row.job_id, srt_text=EXTRACTED_SRT, payload=extracted))


def write_subtitles(store: R2ObjectStore, jobs: Sequence[SeedJob] = TRANSCRIBED_JOBS) -> None:
    """Write the subtitle of every `ok` job where the worker writes it."""
    for seeded in jobs:
        if seeded.status == "ok":
            key = result_srt_key(FIRST_BATCH_ID, seeded.item_id, seeded.language)
            store.write(key, SUBTITLE, "application/x-subrip")


def request(action: str, **fields: object) -> str:
    """Return a request document on the first batch."""
    return json.dumps({"action": action, "batch_id": FIRST_BATCH_ID, **fields})


def placement(strategy: str = "next_to_media", **fields: object) -> dict[str, object]:
    """Return placement options; every strategy but `next_to_media` writes below `OUTPUT`."""
    if strategy != "next_to_media":
        fields.setdefault("output_folder", OUTPUT)
    return {"strategy": strategy, **fields}


def send(leg: Leg, template: str) -> Response:
    """Send a request document (with `<root>`) to the leg's backend."""
    return leg.backend.execute_json(leg.document(template))


def ok_document(response: Response) -> Document:
    """Return a successful response as a dict."""
    assert response.status == "ok", response
    found: Document = json.loads(response.model_dump_json())
    return found


def expected_outcomes(
    *,
    film: str = "place",
    podcast: str = "skip_empty",
    episode: str = "skip_failed",
    silent: str = "place",
) -> dict[str, str]:
    """Return the outcome per item id of a download of the transcribed batch (empty results skipped by default)."""
    return {FILM_ID: film, PODCAST_ID: podcast, EPISODE_ID: episode, SILENT_ID: silent}


def error_document(response: Response) -> Document:
    """Return the error body of an error response as a dict."""
    assert response.status == "error", response
    found: Document = json.loads(response.error.model_dump_json())
    return found


def error_of(response: Response) -> tuple[str, Document]:
    """Return the code and details of an error response."""
    body = error_document(response)
    return body["code"], body["details"]


def plan_error_codes(response: Response) -> list[str]:
    """Return the plan error codes of a `download_plan_invalid` error response."""
    body = error_document(response)
    assert body["code"] == "download_plan_invalid", body
    return [error["code"] for error in body["details"]["errors"]]


def outcomes(document: Document) -> dict[str, str]:
    """Return the outcome per item id of a download response."""
    return {entry["item_id"]: entry["outcome"] for entry in document["download"]["placements"]}


def phase_of(leg: Leg) -> str:
    """Return the first batch's phase."""
    with leg.ports.state.session() as session:
        row = session.get(BatchRow, FIRST_BATCH_ID)
        assert row is not None
        return row.phase


def recorded(leg: Leg) -> list[tuple[int, str]]:
    """Return the recorded placement outcomes as (job id, outcome), in order."""
    with leg.ports.state.session() as session:
        rows = session.execute(select(PlacementRow.job_id, PlacementRow.outcome).order_by(PlacementRow.placement_id))
        return [(job_id, outcome) for job_id, outcome in rows]


def temp_files(root: Path) -> list[Path]:
    """Return every temp file of a download left under the root."""
    return sorted(root.glob("**/*.tmp"))
