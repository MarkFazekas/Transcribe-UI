"""The choices of a transcription of a file in review, and their labels (architecture §3.1 "GUI" "Review and upload").

Qt-free. A transcription (a backend job) is made by transcribing one audio track of its file
(none for an audio file, which is uploaded as it is) or by extracting one embedded subtitle
stream. Only text streams can be extracted (the backend's stream `kind`): picture-based ones
(DVD/Blu-ray) are listed in the file's details, and their transcriptions offer transcribe only.
Which choice fits the file stays the backend's to check (`resolve_review`); this module only
lists the probed tracks and streams.
"""

from dataclasses import dataclass

from transcribe_ui.batches.classes import ExtractChoice, ReviewChoice, TranscribeChoice
from transcribe_ui.batches.responses import JobView, ReviewFile
from transcribe_ui.gui.review.constants import AUDIO_KIND, LIST_SEPARATOR, TEXT_SUBTITLE
from transcribe_ui.i18n.catalog import ntr, tr
from transcribe_ui.media.classes import AudioTrack, SubtitleStream


@dataclass(frozen=True)
class ChoiceOption:
    """One choice a transcription can take, with its label."""

    choice: ReviewChoice
    label: str


def current_choice(item_id: str, job: JobView) -> ReviewChoice | None:
    """Return a transcription's stored choice as a request choice (None: an extraction without a stream)."""
    if job.kind == "extract":
        if job.stream_index is None:
            return None
        return ExtractChoice(kind="extract", item_id=item_id, language=job.language, stream_index=job.stream_index)
    return TranscribeChoice(kind="transcribe", item_id=item_id, language=job.language, track_index=job.track_index)


def _language_tag(tag: str | None) -> str:
    return tr("no language tag") if tag is None else tag


def track_text(track: AudioTrack) -> str:
    """Return an audio track's language tag, codec, channels, default flag and title."""
    parts = [_language_tag(track.language), track.codec]
    channels = track.channels
    if channels is not None:
        parts.append(ntr("{count} channel", "{count} channels", channels).format(count=channels))
    if track.default:
        parts.append(tr("default"))
    if track.title:
        parts.append(f"“{track.title}”")
    return LIST_SEPARATOR.join(parts)


def stream_text(stream: SubtitleStream) -> str:
    """Return a subtitle stream's language tag, codec, flags and title (a picture-based one says so)."""
    parts = [_language_tag(stream.language), stream.codec]
    if stream.kind != TEXT_SUBTITLE:
        parts.append(tr("picture-based: transcribe only"))
    if stream.forced:
        parts.append(tr("forced"))
    if stream.default:
        parts.append(tr("default"))
    if stream.title:
        parts.append(f"“{stream.title}”")
    return LIST_SEPARATOR.join(parts)


def _transcribe(item_id: str, language: str, track_index: int | None, label: str) -> ChoiceOption:
    choice = TranscribeChoice(kind="transcribe", item_id=item_id, language=language, track_index=track_index)
    return ChoiceOption(choice, label)


def _transcribe_options(review_file: ReviewFile, language: str) -> list[ChoiceOption]:
    tracks = review_file.audio_tracks
    item_id = review_file.item_id
    if not tracks:
        return []
    if review_file.kind == AUDIO_KIND:
        return [_transcribe(item_id, language, None, tr("Transcribe"))]
    if len(tracks) == 1:
        return [_transcribe(item_id, language, tracks[0].index, tr("Transcribe"))]
    options = []
    for track in tracks:
        shown = {"number": track.index + 1, "track": track_text(track)}
        label = tr("Transcribe track {number}: {track}").format_map(shown)
        options.append(_transcribe(item_id, language, track.index, label))
    return options


def _extract_options(review_file: ReviewFile, language: str) -> list[ChoiceOption]:
    options = []
    for stream in review_file.subtitle_streams:
        if stream.kind == TEXT_SUBTITLE:
            choice = ExtractChoice(
                kind="extract",
                item_id=review_file.item_id,
                language=language,
                stream_index=stream.index,
            )
            label = tr("Extract subtitle {number}: {stream}").format(
                number=stream.index + 1,
                stream=stream_text(stream),
            )
            options.append(ChoiceOption(choice, label))
    return options


def job_options(review_file: ReviewFile, language: str) -> tuple[ChoiceOption, ...]:
    """Return what a transcription of the file can be: transcribe a track, or extract a text subtitle."""
    return (*_transcribe_options(review_file, language), *_extract_options(review_file, language))


def option_index(options: tuple[ChoiceOption, ...], choice: ReviewChoice | None) -> int | None:
    """Return the position of `choice` among the options (None when it is not one of them)."""
    for index, option in enumerate(options):
        if option.choice == choice:
            return index
    return None
