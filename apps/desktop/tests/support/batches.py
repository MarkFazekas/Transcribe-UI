"""Shared tree, fake probes, ports and request builders of the batch tests (plan m5 M5.8)."""

import json
from collections.abc import Sequence
from datetime import timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from support.discovery import include
from support.fakes import FIXED_NOW, seeded_ports
from support.parity import Exchange
from support.trees import build_tree
from transcribe_contracts.layout import audio_key
from transcribe_ui.api.ports import Ports
from transcribe_ui.batches.payloads import BatchPayload, ItemPayload, JobPayload
from transcribe_ui.media.classes import AudioTrack, ProbeResult, SubtitleStream
from transcribe_ui.media.fake import FakeMediaTools
from transcribe_ui.state.models import AudioObjectRow, BatchRow, ItemRow, JobRow
from transcribe_ui.state.store import LocalState

# Scanned folder inside each leg's root.
MEDIA = "media"
# The scan root of the request documents.
MEDIA_ROOT = f"<root>/{MEDIA}"
# The local time zone of the batch tests: UTC+1, so the fixed 12:00 UTC is 13:00 local.
LOCAL_ZONE = timezone(timedelta(hours=1))
# Batch id of the first batch created at the fixed clock in `LOCAL_ZONE`.
FIRST_BATCH_ID = "batch_20261127_130000"
# Batch id of the second batch created in the same second.
SECOND_BATCH_ID = "batch_20261127_130000_2"

# A video with a Hungarian and an English (default) audio track, an English text subtitle and a
# Hungarian picture subtitle; it also has an English sidecar subtitle.
FILM = "Film.mkv"
# A video without audio, with a Hungarian text subtitle.
SILENT = "Silent.mkv"
# A video with one untagged audio track (the fake's default probe).
EPISODE = "Episode.mkv"
# An audio file (the fake's default probe).
PODCAST = "podcast.mp3"
# Every media file of `BATCH_TREE`, in scan order.
EVERY_FILE = (EPISODE, PODCAST, FILM, SILENT)
BATCH_TREE = (
    f"{MEDIA}/Audio/{PODCAST}",
    f"{MEDIA}/Movies/{FILM}",
    f"{MEDIA}/Movies/Film.en.srt",
    f"{MEDIA}/Movies/{SILENT}",
    f"{MEDIA}/Audio/{EPISODE}",
)
# Size of the fake audio objects seeded into an uploading batch.
SEEDED_OBJECT_BYTES = 16
# Length of `FILM` in the fake probe (90 minutes).
FILM_SECONDS = 5400
# Length of `SILENT` in the fake probe.
SILENT_SECONDS = 60

FILM_PROBE = ProbeResult(
    duration_s=FILM_SECONDS,
    start_s=0,
    has_video=True,
    audio_tracks=(
        AudioTrack(index=0, codec="aac", language="hun", title=None, default=False, channels=2),
        AudioTrack(index=1, codec="opus", language="eng", title="Original", default=True, channels=6),
    ),
    subtitle_streams=(
        SubtitleStream(index=0, codec="subrip", kind="text", language="eng", title=None, default=False, forced=False),
        SubtitleStream(
            index=1,
            codec="hdmv_pgs_subtitle",
            kind="bitmap",
            language="hun",
            title=None,
            default=False,
            forced=False,
        ),
    ),
)
SILENT_PROBE = ProbeResult(
    duration_s=SILENT_SECONDS,
    start_s=0,
    has_video=True,
    audio_tracks=(),
    subtitle_streams=(
        SubtitleStream(index=0, codec="ass", kind="text", language="hun", title=None, default=True, forced=False),
    ),
)


def build_batch_tree(root: Path) -> None:
    """Create `BATCH_TREE` under the root (empty files with fixed mtimes)."""
    build_tree(root, BATCH_TREE)


def media_dir(root: Path) -> Path:
    """Return the scanned folder inside a root."""
    return root / MEDIA


def fake_media() -> FakeMediaTools:
    """Return fake media tools that know `FILM` and `SILENT`."""
    return FakeMediaTools(probes={FILM: FILM_PROBE, SILENT: SILENT_PROBE})


def batch_ports(root: Path) -> Ports:
    """Return the seeded test ports with `fake_media` and `LOCAL_ZONE`."""
    return seeded_ports(root, media=fake_media(), local_zone=LOCAL_ZONE)


def composition(*languages: str, paths: Sequence[str] = (MEDIA_ROOT,), **fields: object) -> dict[str, object]:
    """Return the fields of a batch composition including the files and folders at `paths`."""
    return {
        "scan": {"root": MEDIA_ROOT},
        "operations": [include(path) for path in paths],
        "languages": list(languages),
        **fields,
    }


def create_request(*languages: str, paths: Sequence[str] = (MEDIA_ROOT,), **fields: object) -> str:
    """Return a `create_batch` request document."""
    return json.dumps({"action": "create_batch", **composition(*languages, paths=paths, **fields)})


def batch_request(action: str, batch_id: str = FIRST_BATCH_ID, **fields: object) -> str:
    """Return a request document of a batch action."""
    return json.dumps({"action": action, "batch_id": batch_id, **fields})


def transcribe(item_id: str, language: str, track_index: int | None = None) -> dict[str, object]:
    """Return a `transcribe` choice of `resolve_review`."""
    return {"kind": "transcribe", "item_id": item_id, "language": language, "track_index": track_index}


def extract(item_id: str, language: str, stream_index: int) -> dict[str, object]:
    """Return an `extract` choice of `resolve_review`."""
    return {"kind": "extract", "item_id": item_id, "language": language, "stream_index": stream_index}


def list_request(*, include_hidden: bool = False) -> str:
    """Return a `list_batches` request document."""
    return json.dumps({"action": "list_batches", "include_hidden": include_hidden})


def response_of(exchange: Exchange) -> dict[str, object]:
    """Return the normalised response of an exchange as a dict."""
    document: dict[str, object] = json.loads(exchange.response)
    return document


def item_ids(exchange: Exchange) -> dict[str, str]:
    """Return file name -> item id from a response carrying `batch` (a `BatchDetail`)."""
    batch = response_of(exchange)["batch"]
    assert isinstance(batch, dict)
    found = batch["items"]
    assert isinstance(found, list)
    return {member["name"]: member["item_id"] for member in found}


def seed_uploading_batch(state: LocalState, root: Path, *, manifest_held: bool = True) -> dict[str, str]:
    """Store a batch in `uploading` with `FILM` and `PODCAST` uploaded; return file name -> item id.

    Each item has one recorded audio object; the caller uploads the objects if it needs them.
    """
    media = media_dir(root)
    payload = BatchPayload(scan_root=str(media), path_flavor="posix", languages=("en",))
    ids = {FILM: "01JDZZZZZZZZZZZZZZZZZZZZ01", PODCAST: "01JDZZZZZZZZZZZZZZZZZZZZ02"}
    with state.session() as session:
        session.add(
            BatchRow(
                batch_id=FIRST_BATCH_ID,
                phase="uploading",
                manifest_held=manifest_held,
                audio_deleted=False,
                hidden=False,
                created_at=FIXED_NOW,
                updated_at=FIXED_NOW,
                payload=payload.model_dump(mode="json"),
            ),
        )
        session.flush()
        for position, (name, folder, probe) in enumerate(
            ((FILM, "Movies", FILM_PROBE), (PODCAST, "Audio", None)),
        ):
            _seed_item(session, ids[name], position, media / folder / name, probe)
    return ids


def _seed_item(session: Session, item_id: str, position: int, path: Path, probe: ProbeResult | None) -> None:
    stat = path.stat()
    kind = "video" if path.suffix == ".mkv" else "audio"
    found = probe or FakeMediaTools().default_probe
    stored = ItemPayload(
        position=position,
        path=str(path),
        relative_path=f"{path.parent.name}/{path.name}",
        name=path.name,
        kind=kind,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        subtitle_languages=(),
        probe=found,
    )
    session.add(
        ItemRow(item_id=item_id, batch_id=FIRST_BATCH_ID, phase="uploaded", payload=stored.model_dump(mode="json"))
    )
    session.flush()
    track = 1 if kind == "video" else None
    key = audio_key(FIRST_BATCH_ID, item_id, "mka", track)
    job = JobPayload(mode="normal", track_index=track)
    session.add(
        JobRow(
            item_id=item_id,
            batch_id=FIRST_BATCH_ID,
            language="en",
            kind="transcribe",
            status="pending",
            payload=job.model_dump(mode="json"),
        ),
    )
    session.add(AudioObjectRow(object_key=key, batch_id=FIRST_BATCH_ID, item_id=item_id, status="verified", payload={}))


def seeded_keys(state: LocalState) -> list[str]:
    """Return the recorded audio object keys of the seeded batch."""
    with state.session() as session:
        return list(session.scalars(select(AudioObjectRow.object_key).order_by(AudioObjectRow.object_key)))
