"""Putting one audio track of an item into R2 and recording it (architecture §3.3 "Upload").

An audio file is uploaded as it is; a video's track is converted into the batch's temp folder
first and the temp file is deleted afterwards; a retry item copies the source batch's object
server side. Each object is verified by its size; for a local file the source-unchanged check
runs again before the object is recorded.
"""

import re
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from transcribe_contracts.layout import audio_key
from transcribe_ui.api.classes import ProgressEvent
from transcribe_ui.batches.records import StoredItem
from transcribe_ui.upload.constants import AUDIO_EXTENSION_RULE, FLAC_CODEC_NAME, UPLOAD_BYTES_STAGE
from transcribe_ui.upload.errors import (
    AudioUnusableError,
    CopySourceMissingError,
    UploadUnverifiedError,
)
from transcribe_ui.upload.payloads import AudioObjectPayload, CopySource
from transcribe_ui.upload.rows import record_audio
from transcribe_ui.upload.sources import UploadRun, media_call, probe_of, require_unchanged, unreadable_error
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
    source = _copy_source(member, track)
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
    cancel = run.context.cancel
    converted = media_call(member, lambda: media.convert(source, track, base, cancel))
    codec = FLAC_CODEC_NAME if converted.fell_back else probe_of(member).audio_tracks[track].codec
    with ExitStack() as cleanup:
        cleanup.callback(remove_temp, converted.path)
        encoding = _Encoding(converted.extension, codec)
        _send(run, member, (converted.path, track), encoding)


def _send(run: UploadRun, member: StoredItem, local: _LocalTrack, encoding: _Encoding) -> None:
    """Upload a local file as the track's object, verify it, check the source again, record it.

    A file that cannot be read, or an object that does not verify, is first checked against the
    source: a changed source sends the item back to review (the failure was its consequence);
    otherwise the error stands and re-sending resumes.
    """
    path, track = local
    key = audio_key(member.row.batch_id, member.item_id, encoding.extension, track)
    try:
        size = _upload(run, member, key, path)
    except OSError as error:
        require_unchanged(member)
        raise unreadable_error(member, path) from error
    except UploadUnverifiedError:
        require_unchanged(member)
        raise
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


def _upload(run: UploadRun, member: StoredItem, key: str, path: Path) -> int:
    """Upload the local file to `key` and verify it; return its size.

    Raises:
        OSError: The file cannot be read.
        AudioUnusableError: The file is empty.
        UploadUnverifiedError: The object could not be verified.
    """
    size = path.stat().st_size
    if not size:
        raise AudioUnusableError(member.item_id)
    reporter = _BytesReporter(run, member.payload.path, size)
    local = LocalFile(path, size)
    upload_object(run.store, key, local, reporter, run.context.cancel)
    return size


def _copy_source(member: StoredItem, track: int | None) -> CopySource:
    """Return the item's copy source of the track.

    Raises:
        CopySourceMissingError: The item has none for this track.
    """
    for source in member.payload.copy_sources:
        if source.audio.track_index == track:
            return source
    raise CopySourceMissingError(member.item_id, track)
