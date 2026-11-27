"""Putting one audio track of an item into R2 and recording it (architecture §3.3 "Upload").

An audio file is uploaded as it is; a video's track is converted into the batch's temp folder
first and the temp file is deleted afterwards; a retry item copies the source batch's object
server side. Each object is verified by its size; for a local file the source-unchanged check
runs again before the object is recorded.
"""

import re
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from transcribe_contracts.layout import audio_key
from transcribe_ui.api.classes import ProgressEvent
from transcribe_ui.batches.records import StoredItem
from transcribe_ui.upload.constants import AUDIO_EXTENSION_RULE, FLAC_CODEC_NAME, UPLOAD_BYTES_STAGE
from transcribe_ui.upload.errors import AudioUnusableError
from transcribe_ui.upload.payloads import AudioObjectPayload, CopySource
from transcribe_ui.upload.rows import record_audio
from transcribe_ui.upload.sources import SourceChangedError, UploadRun, probe_of, require_unchanged
from transcribe_ui.upload.temp import remove_temp
from transcribe_ui.upload.transfer import LocalFile, copy_object, upload_object

# A local audio file and the track it holds (None: an audio file uploaded as it is).
type _LocalTrack = tuple[Path, int | None]


@dataclass(frozen=True)
class _Encoding:
    """How a local audio file is stored: its extension and codec."""

    extension: str
    codec: str


def put_track(run: UploadRun, member: StoredItem, track: int | None) -> None:
    """Upload (or copy) the item's audio object of one track, verify it and record it.

    Raises:
        SourceChangedError: The file changed or vanished (nothing was recorded).
        AudioUnusableError: The audio cannot be uploaded as it is.
        UploadUnverifiedError: The object could not be verified.
        DomainError: A media tool, R2 or the database failed, or the request was cancelled.
    """
    if member.payload.copy_sources:
        _copy_track(run, member, track)
    elif track is None:
        _upload_as_is(run, member)
    else:
        _upload_converted(run, member, track)


def _copy_track(run: UploadRun, member: StoredItem, track: int | None) -> None:
    source = _copy_source(member.payload.copy_sources, track)
    audio = source.audio
    key = audio_key(member.row.batch_id, member.item_id, audio.extension, track)
    copy_object(run.store, source.source_key, key, audio.size_bytes)
    record_audio(run.context, member, key, audio)


def _upload_as_is(run: UploadRun, member: StoredItem) -> None:
    source = Path(member.payload.path)
    extension = source.suffix.removeprefix(".").lower()
    tracks = probe_of(member).audio_tracks
    if re.fullmatch(AUDIO_EXTENSION_RULE, extension) is None or not tracks:
        raise AudioUnusableError(member.item_id)
    encoding = _Encoding(extension, tracks[0].codec)
    _send(run, member, (source, None), encoding)


def _upload_converted(run: UploadRun, member: StoredItem, track: int) -> None:
    base = run.temp_folder / f"{member.item_id}.a{track}"
    media = run.context.ports.media
    source = Path(member.payload.path)
    converted = media.convert(source, track, base, run.context.cancel)
    codec = FLAC_CODEC_NAME if converted.fell_back else probe_of(member).audio_tracks[track].codec
    with ExitStack() as cleanup:
        cleanup.callback(remove_temp, converted.path)
        encoding = _Encoding(converted.extension, codec)
        _send(run, member, (converted.path, track), encoding)


def _send(run: UploadRun, member: StoredItem, local: _LocalTrack, encoding: _Encoding) -> None:
    """Upload a local file as the track's object, verify it, check the source again, record it."""
    path, track = local
    size = _size_of(member, path)
    key = audio_key(member.row.batch_id, member.item_id, encoding.extension, track)
    reporter = _BytesReporter(run, member.payload.path, size)
    cancel = run.context.cancel
    try:
        upload_object(run.store, key, LocalFile(path, size), reporter, cancel)
    except OSError as error:
        raise SourceChangedError from error
    require_unchanged(member)
    audio = AudioObjectPayload(
        track_index=track,
        extension=encoding.extension,
        codec=encoding.codec,
        duration_s=probe_of(member).duration_s or 0,
        size_bytes=size,
    )
    record_audio(run.context, member, key, audio)


@dataclass(frozen=True)
class _BytesReporter:
    """Turns the upload's byte counts into progress events."""

    run: UploadRun
    path: str
    size: int

    def __call__(self, sent: int) -> None:
        """Report the bytes sent so far."""
        event = _bytes_event(self.run.action, sent, self.size, self.path)
        self.run.context.progress.emit(event)


def _bytes_event(action: str, sent: int, size: int, path: str) -> ProgressEvent:
    return ProgressEvent(action=action, stage=UPLOAD_BYTES_STAGE, completed=sent, total=size, path=path)


def _size_of(member: StoredItem, path: Path) -> int:
    """Return the local file's size; an empty file cannot be uploaded.

    Raises:
        SourceChangedError: The file cannot be read (it vanished).
        AudioUnusableError: The file is empty.
    """
    try:
        size = path.stat().st_size
    except OSError as error:
        raise SourceChangedError from error
    if not size:
        raise AudioUnusableError(member.item_id)
    return size


def _copy_source(sources: Sequence[CopySource], track: int | None) -> CopySource:
    for source in sources:
        if source.audio.track_index == track:
            return source
    msg = "a retry item has a copy source for every track its jobs use"
    raise LookupError(msg)
