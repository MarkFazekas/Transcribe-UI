"""The rows of an adopted batch, from another computer's manifest (`adopt_cloud_batch`, architecture §3.9).

Each item's path below the scan root is split with the manifest's own path flavour and refused
(`manifest_path_invalid`) when a part is no single file or folder name on every OS
(`placement/naming.py` `unsafe_part`). The batch is recorded `ready`, created when the manifest
was; its items `uploaded` with their original paths as stored, their jobs pending and their audio
objects verified (the remaining-time estimate reads their durations).
"""

from datetime import datetime
from pathlib import PurePath, PurePosixPath, PureWindowsPath

from sqlalchemy.orm import Session

from transcribe_contracts.classes import Manifest, ManifestItem, PathFlavor
from transcribe_ui.batches.constants import ADOPTED_PHASE, JOB_PENDING
from transcribe_ui.batches.errors import BatchExistsError, ManifestPathInvalidError
from transcribe_ui.batches.payloads import AdoptedBatch, BatchPayload, ItemPayload, JobPayload
from transcribe_ui.placement.naming import unsafe_part
from transcribe_ui.state.models import AudioObjectRow, BatchRow, ItemRow, JobRow
from transcribe_ui.upload.constants import AUDIO_VERIFIED, ITEM_UPLOADED
from transcribe_ui.upload.payloads import AudioObjectPayload

# The folder and file names of one item below the scan root.
type NameParts = tuple[str, ...]
# Those names, by item id.
type RelativeParts = dict[str, NameParts]


def relative_paths(manifest: Manifest) -> RelativeParts:
    """Return each item's folder and file names below the scan root, split in the manifest's flavour.

    Raises:
        ManifestPathInvalidError: A part is empty, `.` or `..`, holds a separator or NUL, or reads as
            a drive or an anchor in either flavour (the first such item is named).
    """
    flavor = manifest.path_flavor
    root = _pure(manifest.scan_root, flavor)
    found: RelativeParts = {}
    for entry in manifest.items:
        path = _pure(entry.original_path, flavor)
        parts = path.relative_to(root).parts
        if not parts or any(map(unsafe_part, parts)):
            raise ManifestPathInvalidError(manifest.batch_id, entry.item_id)
        found[entry.item_id] = parts
    return found


def _pure(path: str, flavor: PathFlavor) -> PurePath:
    return PureWindowsPath(path) if flavor == "windows" else PurePosixPath(path)


def record_adopted(session: Session, manifest: Manifest, relative: RelativeParts, now: datetime) -> None:
    """Insert the batch (`ready`, adopted), its items (`uploaded`), their pending jobs and verified audio rows."""
    batch_id = manifest.batch_id
    languages = sorted({job.language for entry in manifest.items for job in entry.jobs})
    adopted = AdoptedBatch(uploaded_by=manifest.uploaded_by)
    payload = BatchPayload(
        scan_root=manifest.scan_root,
        path_flavor=manifest.path_flavor,
        languages=tuple(languages),
        adopted=adopted,
    )
    batch = BatchRow(
        batch_id=batch_id,
        phase=ADOPTED_PHASE,
        audio_deleted=False,
        hidden=False,
        created_at=manifest.created_at,
        updated_at=now,
        payload=payload.model_dump(mode="json"),
    )
    session.add(batch)
    session.flush()
    for position, entry in enumerate(manifest.items):
        _record_item(session, batch_id, position, entry, relative[entry.item_id])


def _record_item(session: Session, batch_id: str, position: int, entry: ManifestItem, parts: NameParts) -> None:
    relative = "/".join(parts)
    payload = ItemPayload(
        position=position,
        path=entry.original_path,
        relative_path=relative,
        name=parts[-1],
        kind=entry.media_kind,
        size_bytes=entry.source_size,
        mtime_ns=entry.source_mtime_ns,
        subtitle_languages=(),
    )
    stored = payload.model_dump(mode="json")
    row = ItemRow(item_id=entry.item_id, batch_id=batch_id, phase=ITEM_UPLOADED, payload=stored)
    session.add(row)
    session.flush()
    tracks = {audio.object_key: audio.track_index for audio in entry.audio}
    for job in entry.jobs:
        job_payload = JobPayload(mode=job.mode, track_index=tracks[job.audio_object_key])
        session.add(
            JobRow(
                item_id=entry.item_id,
                batch_id=batch_id,
                language=job.language,
                kind="transcribe",
                status=JOB_PENDING,
                payload=job_payload.model_dump(mode="json"),
            ),
        )
    for audio in entry.audio:
        object_payload = AudioObjectPayload(
            track_index=audio.track_index,
            extension=audio.extension,
            codec=audio.codec,
            duration_s=audio.duration_s,
            size_bytes=audio.size_bytes,
        )
        session.add(
            AudioObjectRow(
                object_key=audio.object_key,
                batch_id=batch_id,
                item_id=entry.item_id,
                status=AUDIO_VERIFIED,
                payload=object_payload.model_dump(mode="json"),
            ),
        )


def require_new(session: Session, batch_id: str) -> None:
    """Refuse an id this database already uses (ids are unique only per database).

    Raises:
        BatchExistsError: A batch of this id exists here.
    """
    if session.get(BatchRow, batch_id) is not None:
        raise BatchExistsError(batch_id)
