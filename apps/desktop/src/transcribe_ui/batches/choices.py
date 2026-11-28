"""Job choices of the review step: pre-selection and validity (architecture §3.3 "Review step").

A job's choice is its kind (`transcribe` or `extract`) plus the audio track or subtitle stream
it uses. After a probe every job gets a pre-selected choice: transcribe from the track tagged
with the job's language, otherwise the default-flagged track, otherwise the first; an audio file
has no track choice (it is uploaded as it is). A file without audio extracts an embedded text
subtitle of the job's language when it has one, else its job stays unresolved.
"""

from transcribe_ui.batches.constants import (
    DEFAULT_MODE,
    REASON_NO_AUDIO,
    REASON_NOT_PROBED,
    REASON_STREAM_MISSING,
    REASON_STREAM_NOT_TEXT,
    REASON_STREAM_OUT_OF_RANGE,
    REASON_TRACK_MISSING,
    REASON_TRACK_NOT_ALLOWED,
    REASON_TRACK_OUT_OF_RANGE,
)
from transcribe_ui.batches.payloads import ItemPayload, JobKind, JobPayload
from transcribe_ui.discovery.languages import language_of
from transcribe_ui.media.classes import AudioTrack, ProbeResult, SubtitleStream


def _tagged(tag: str | None, language: str) -> bool:
    return tag is not None and language_of(tag) == language


def preselected_track(tracks: tuple[AudioTrack, ...], language: str) -> int | None:
    """Return the track tagged with the language, else the default-flagged one, else the first."""
    tagged = [track.index for track in tracks if _tagged(track.language, language)]
    default = [track.index for track in tracks if track.default]
    first = [track.index for track in tracks[:1]]
    found = [*tagged, *default, *first]
    return found[0] if found else None


def tagged_text_stream(streams: tuple[SubtitleStream, ...], language: str) -> int | None:
    """Return the first text subtitle stream tagged with the language (an extraction's stream), or None."""
    texts = [stream for stream in streams if stream.kind == "text"]
    tagged = [stream.index for stream in texts if _tagged(stream.language, language)]
    return tagged[0] if tagged else None


def preselected_choice(member: ItemPayload, probe: ProbeResult, language: str) -> tuple[JobKind, JobPayload]:
    """Return the pre-selected choice of a job of a probed item."""
    tracks = probe.audio_tracks
    if not tracks:
        stream = tagged_text_stream(probe.subtitle_streams, language)
        if stream is not None:
            return "extract", JobPayload(mode=DEFAULT_MODE, stream_index=stream)
    track = preselected_track(tracks, language) if member.kind == "video" else None
    return "transcribe", JobPayload(mode=DEFAULT_MODE, track_index=track)


def choice_problem(member: ItemPayload, kind: JobKind, job: JobPayload) -> str | None:
    """Return why the choice does not fit the probed item, or None when it does."""
    probe = member.probe
    if probe is None or member.problem is not None:
        return REASON_NOT_PROBED
    if kind == "extract":
        return _stream_problem(probe, job.stream_index)
    return _track_problem(member, probe, job.track_index)


def _track_problem(member: ItemPayload, probe: ProbeResult, index: int | None) -> str | None:
    if not probe.audio_tracks:
        return REASON_NO_AUDIO
    if member.kind == "audio":
        return None if index is None else REASON_TRACK_NOT_ALLOWED
    if index is None:
        return REASON_TRACK_MISSING
    return None if index < len(probe.audio_tracks) else REASON_TRACK_OUT_OF_RANGE


def _stream_problem(probe: ProbeResult, index: int | None) -> str | None:
    if index is None:
        return REASON_STREAM_MISSING
    if index >= len(probe.subtitle_streams):
        return REASON_STREAM_OUT_OF_RANGE
    return None if probe.subtitle_streams[index].kind == "text" else REASON_STREAM_NOT_TEXT
