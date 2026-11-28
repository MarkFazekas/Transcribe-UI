"""A batch's manifest, built from the database (architecture §4 "Manifest").

The manifest lists every item with `transcribe` jobs, each with the audio objects its jobs use
and its jobs by language; `extract` jobs never appear (they are local). It is built only for a
complete upload: every item `uploaded` and every audio object a job uses verified. The same
database state always gives the same bytes (`created_at` is the batch's creation time), so a
manifest written again after an interruption is the same object.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from transcribe_contracts.classes import AudioObject, Manifest, ManifestItem, TranscribeJob
from transcribe_contracts.layout import manifest_key
from transcribe_ui.batches.payloads import BatchPayload
from transcribe_ui.batches.phases import load_batch
from transcribe_ui.batches.records import StoredItem, StoredJob, stored_items
from transcribe_ui.state.models import AudioObjectRow, BatchRow
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.constants import AUDIO_VERIFIED, ITEM_UPLOADED, MANIFEST_CONTENT_TYPE
from transcribe_ui.upload.errors import NothingToTranscribeError, UploadIncompleteError
from transcribe_ui.upload.payloads import AudioObjectPayload

# The verified audio objects of one item, by audio track (None: an audio file uploaded as it is).
type ItemAudio = Mapping[int | None, AudioObject]


def build_manifest(session: Session, batch_id: str) -> Manifest:
    """Return the batch's manifest as the database describes it now.

    Raises:
        BatchNotFoundError: If there is no such batch.
        UploadIncompleteError: If an item is not `uploaded` or a job's audio object is missing
            or not verified.
        NothingToTranscribeError: If no item has a `transcribe` job (the extract-only path).
    """
    row = load_batch(session, batch_id)
    payload = BatchPayload.model_validate(row.payload)
    members = stored_items(session, batch_id)
    entries, not_ready = _collect(members, _verified_audio(session, batch_id))
    if not_ready:
        raise UploadIncompleteError(batch_id, not_ready)
    if not entries:
        raise NothingToTranscribeError(batch_id)
    return Manifest(
        batch_id=batch_id,
        created_at=row.created_at,
        scan_root=payload.scan_root,
        path_flavor=payload.path_flavor,
        items=tuple(entries),
    )


def manifest_body(manifest: Manifest) -> bytes:
    """Return the manifest as stored in R2 (compact JSON, UTF-8)."""
    return manifest.model_dump_json().encode()


def write_manifest(store: ObjectStore, manifest: Manifest) -> None:
    """Upload (or upload again) the manifest to `batch_metadata/<batch_id>.json`.

    Raises:
        StorageError: R2 refused or could not be reached.
    """
    store.write(manifest_key(manifest.batch_id), manifest_body(manifest), MANIFEST_CONTENT_TYPE)


def withdrawing_batch_ids(session: Session) -> list[str]:
    """Return the ids of the `uploading` batches with a pending withdrawal of a leftover manifest, sorted."""
    pending = (BatchRow.phase == "uploading", BatchRow.pending_withdrawal.is_not(None))
    query = select(BatchRow.batch_id).where(*pending)
    return list(session.scalars(query.order_by(BatchRow.batch_id)))


def _collect(
    members: Sequence[StoredItem],
    audio: Mapping[str, ItemAudio],
) -> tuple[list[ManifestItem], list[str]]:
    """Return the manifest entries and the ids of the items that are not ready yet."""
    entries: list[ManifestItem] = []
    not_ready: list[str] = []
    for member in members:
        jobs = [job for job in member.jobs if job.kind == "transcribe"]
        entry = _manifest_item(member, jobs, audio.get(member.item_id, {}))
        complete = entry is not None or not jobs
        if member.phase != ITEM_UPLOADED or not complete:
            not_ready.append(member.item_id)
        elif entry is not None:
            entries.append(entry)
    return entries, not_ready


def _verified_audio(session: Session, batch_id: str) -> Mapping[str, ItemAudio]:
    verified = (AudioObjectRow.batch_id == batch_id, AudioObjectRow.status == AUDIO_VERIFIED)
    found: defaultdict[str, dict[int | None, AudioObject]] = defaultdict(dict)
    for row in session.scalars(select(AudioObjectRow).where(*verified)):
        stored = AudioObjectPayload.model_validate(row.payload)
        found[row.item_id][stored.track_index] = AudioObject(
            object_key=row.object_key,
            track_index=stored.track_index,
            extension=stored.extension,
            codec=stored.codec,
            duration_s=stored.duration_s,
            size_bytes=stored.size_bytes,
        )
    return found


def _manifest_item(member: StoredItem, jobs: Sequence[StoredJob], audio: ItemAudio) -> ManifestItem | None:
    """Return the item's manifest entry, or None if it has no `transcribe` job or misses a verified object."""
    used: dict[str, AudioObject] = {}
    entries: list[TranscribeJob] = []
    for job in jobs:
        audio_object = audio.get(job.payload.track_index)
        if audio_object is None:
            return None
        used[audio_object.object_key] = audio_object
        entries.append(_transcribe_job(job, audio_object))
    if not entries:
        return None
    payload = member.payload
    return ManifestItem(
        item_id=member.item_id,
        original_path=payload.path,
        source_size=payload.size_bytes,
        source_mtime_ns=payload.mtime_ns,
        media_kind=payload.kind,
        audio=tuple(used[key] for key in sorted(used)),
        jobs=tuple(entries),
    )


def _transcribe_job(job: StoredJob, audio_object: AudioObject) -> TranscribeJob:
    key = audio_object.object_key
    return TranscribeJob(language=job.row.language, mode=job.payload.mode, audio_object_key=key)
