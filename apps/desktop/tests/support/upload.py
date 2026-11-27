"""The upload tests' tree and requests, backends over moto and the fake Runpod API (plan m5 M5.10)."""

import json
import os
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from types import MappingProxyType

from sqlalchemy import select

from support.batch_backend import ids_of, run_ok
from support.batches import (
    FILM,
    FIRST_BATCH_ID,
    MEDIA,
    MEDIA_ROOT,
    PODCAST,
    SILENT,
    batch_request,
    build_batch_tree,
    create_request,
)
from support.buckets import bucket_store
from support.credentials import TEST_BUCKET, store_request
from support.parity import Leg
from support.runpod import Cloud
from support.runs import RunsWorld, runs_ports
from support.storage import store_client
from support.trees import FIXED_MTIME_NS
from transcribe_contracts.layout import batch_prefix, manifest_key
from transcribe_ui.api.classes import Response
from transcribe_ui.api.ports import CancelFlag, Ports, ProgressSink
from transcribe_ui.state.models import AudioObjectRow, BatchRow, ExtractedSubtitleRow
from transcribe_ui.storage.r2 import R2ObjectStore
from transcribe_ui.upload.temp import temp_dir

# Target languages of the upload tests.
EN = "en"
HU = "hu"
# Bytes of the podcast (an audio file is uploaded as it is, so it must not be empty).
PODCAST_BYTES = b"ID3 fake mp3 audio frames\n" * 8
# The silent video gets only a Hungarian job: it extracts its Hungarian text subtitle.
SILENT_IN_HUNGARIAN = MappingProxyType({"path": f"{MEDIA_ROOT}/Movies/{SILENT}", "languages": (HU,)})
# Create the batch of the upload tests: every file in English and Hungarian, the silent video in Hungarian.
CREATE = create_request(EN, HU, item_languages=[dict(SILENT_IN_HUNGARIAN)], confirm_existing_languages=True)
# The requests that bring the batch to a complete review.
REVIEWED = (CREATE, batch_request("start_review"), batch_request("review_batch"))
# The upload request of the first batch.
UPLOAD = batch_request("run_upload")
# One second in nanoseconds.
ONE_SECOND_NS = 1_000_000_000
# mtime of a source file after the tests "edit" it (a second later than the tree's).
CHANGED_MTIME_NS = FIXED_MTIME_NS + ONE_SECOND_NS

# A batch's objects in R2 (key and size), sorted, and its manifest's bytes (None when absent).
type BucketState = tuple[tuple[tuple[str, int], ...], bytes | None]


def build_upload_tree(root: Path) -> None:
    """Create the batch tree, with bytes in the podcast (fixed mtimes throughout)."""
    build_batch_tree(root)
    podcast = source_path(root, PODCAST)
    podcast.write_bytes(PODCAST_BYTES)
    os.utime(podcast, ns=(FIXED_MTIME_NS, FIXED_MTIME_NS))


def source_path(root: Path, name: str) -> Path:
    """Return the path of a file of the tree."""
    folder = "Audio" if name in {PODCAST, "Episode.mkv"} else "Movies"
    return root / MEDIA / folder / name


def change_source(root: Path, name: str) -> None:
    """Give a file of the tree a new mtime (as an edit would)."""
    os.utime(source_path(root, name), ns=(CHANGED_MTIME_NS, CHANGED_MTIME_NS))


@contextmanager
def upload_world(
    root: Path,
    bucket: str = TEST_BUCKET,
    wrap: Callable[[Ports], Ports] | None = None,
) -> Iterator[RunsWorld]:
    """Return a backend on the upload tree with every credential stored for `bucket` (inside `moto_bucket()`).

    The clock is fixed (the first batch gets `FIRST_BATCH_ID`); `wrap` may replace ports (media tools).
    """
    cloud = Cloud()
    ports = runs_ports(cloud)
    factory = ports if wrap is None else _wrapped(ports, wrap)
    leg = Leg.create(root, None, build_upload_tree, factory)
    with closing(leg):
        stored = leg.backend.execute_json(store_request(r2_bucket=bucket))
        assert stored.status == "ok", stored
        yield RunsWorld(leg, cloud, bucket_store(bucket))


def _wrapped(factory: Callable[[Path], Ports], wrap: Callable[[Ports], Ports]) -> Callable[[Path], Ports]:
    return lambda root: wrap(factory(root))


def review(world: RunsWorld, *documents: str) -> dict[str, str]:
    """Send the requests (by default those of a complete review); return file name -> item id."""
    for document in documents or REVIEWED:
        run_ok(world.leg, document)
    return ids_of(world.leg)


def upload(world: RunsWorld, cancel: CancelFlag | None = None, progress: ProgressSink | None = None) -> Response:
    """Send `run_upload` for the first batch."""
    return world.leg.backend.execute_json(UPLOAD, progress=progress, cancel=cancel)


def outcome_of(response: Response) -> str:
    """Return the outcome of a successful `run_upload`."""
    document = json.loads(response.model_dump_json())
    assert document["status"] == "ok", document
    outcome: str = document["outcome"]
    return outcome


def bucket_state(store: R2ObjectStore, batch_id: str = FIRST_BATCH_ID) -> BucketState:
    """Return the batch's objects (key, size) and its manifest's bytes."""
    listed = tuple((found.key, found.size) for found in store.list_objects(batch_prefix(batch_id)))
    return listed, store.read(manifest_key(batch_id))


def audio_keys(store: R2ObjectStore, batch_id: str = FIRST_BATCH_ID) -> list[str]:
    """Return the keys of the batch's audio objects."""
    return [key for key, _size in bucket_state(store, batch_id)[0] if "/audio/" in key]


def recorded_keys(world: RunsWorld) -> list[str]:
    """Return the keys of the `audio_objects` rows."""
    with world.ports.state.session() as session:
        return list(session.scalars(select(AudioObjectRow.object_key).order_by(AudioObjectRow.object_key)))


def extracted_count(world: RunsWorld) -> int:
    """Return how many extracted subtitles are stored."""
    with world.ports.state.session() as session:
        return len(list(session.scalars(select(ExtractedSubtitleRow.job_id))))


def phase_of(world: RunsWorld, batch_id: str = FIRST_BATCH_ID) -> str:
    """Return the batch's phase."""
    with world.ports.state.session() as session:
        row = session.get(BatchRow, batch_id)
        assert row is not None
        return row.phase


def set_phase(world: RunsWorld, phase: str, batch_id: str = FIRST_BATCH_ID) -> None:
    """Set the batch's phase directly."""
    with world.ports.state.session() as session:
        row = session.get(BatchRow, batch_id)
        assert row is not None
        row.phase = phase


def temp_files(world: RunsWorld, batch_id: str = FIRST_BATCH_ID) -> list[str]:
    """Return the names of the files left in the batch's temp folder."""
    folder = temp_dir(world.ports.state.paths, batch_id)
    return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []


def open_uploads(bucket: str = TEST_BUCKET) -> int:
    """Return how many multipart uploads are open in the bucket."""
    listed = store_client().list_multipart_uploads(Bucket=bucket)
    return len(listed.get("Uploads", []))


# The film's source path under a world's root, for the tests that edit it.
def film_path(world: RunsWorld) -> Path:
    """Return the film's source path."""
    return source_path(world.leg.root, FILM)
