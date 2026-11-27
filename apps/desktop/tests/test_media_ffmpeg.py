"""The media adapter on the real ffprobe and ffmpeg, with tiny generated media (plan m5 M5.4).

ffmpeg's output differs across builds and OSes, so these assert structure: codecs, tags,
counts, durations within a tolerance, cue texts (plan m5 §1 "Golden files").
"""

import os
import re
import struct
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from support import media
from transcribe_ui.api.errors import CancelledError, DomainError
from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.media.adapter import FfmpegMediaTools, MediaTimeouts
from transcribe_ui.media.classes import ProbeResult
from transcribe_ui.media.constants import ERROR_MEDIA_TOOL_FAILED, ERROR_MEDIA_TOOL_TIMEOUT

# Allowed difference between a generated file's length and the probed duration (codec priming).
DURATION_TOLERANCE_S = 0.1
# One SRT cue: number, timing line, text.
SRT_CUE = re.compile(r"(\d+)\n(\d\d:\d\d:\d\d,\d{3}) --> (\d\d:\d\d:\d\d,\d{3})\n(.+?)\n(?:\n|$)", re.DOTALL)
# Longest a killed conversion may take to return (poll interval + kill, with CI slack).
KILL_BUDGET_S = 5.0
# Timeout of a conversion of the endless stream (ffmpeg opens its output well before it).
ENDLESS_TIMEOUT_S = 3.0
# Longest to wait for ffmpeg to open its output on the endless input.
OUTPUT_WAIT_S = 20.0
# Sample rate of the endless WAV stream.
STREAM_RATE_HZ = 16000
# Bytes per sample of the endless stream (16-bit mono).
SAMPLE_BYTES = 2
# Bits in a byte (the WAV header states bits per sample).
BITS_PER_BYTE = 8
# How long the endless stream claims to be.
ANNOUNCED_SECONDS = 3600
# struct layout of a canonical 44-byte PCM WAV header.
WAV_HEADER_LAYOUT = "<4sI4s4sIHHIIHH4sI"
# Size of that header, and of its "RIFF" + size preamble the RIFF size does not count.
WAV_HEADER_BYTES = 44
RIFF_PREAMBLE_BYTES = 8
# Size of the PCM "fmt " chunk and its format tag (1 = integer PCM).
PCM_FORMAT_BYTES = 16
PCM_FORMAT = 1
# Pause of the feeder while the pipe is full.
FEED_PAUSE_S = 0.005
# How often the output watcher looks for the partial file.
WATCH_INTERVAL_S = 0.01
# Bytes the endless stream writes at a time (fits a pipe buffer).
STREAM_CHUNK_BYTES = 4096


@pytest.fixture
def tools() -> FfmpegMediaTools:
    """Return the real adapter (ffmpeg and ffprobe from PATH)."""
    return FfmpegMediaTools()


def _probe(tools: FfmpegMediaTools, path: Path) -> ProbeResult:
    return tools.probe(path, CancelFlag())


def _cue_texts(srt: str) -> list[str]:
    return [match.group(4) for match in SRT_CUE.finditer(srt)]


def test_probe_reads_tracks_tags_and_subtitles(tmp_path: Path, tools: FfmpegMediaTools) -> None:
    """Audio tracks keep their order, codec, language, title and default flag; subtitles their kind."""
    probed = _probe(tools, media.multitrack_video(tmp_path))

    audio = [(track.index, track.codec, track.language, track.title, track.default) for track in probed.audio_tracks]
    subtitles = [(stream.index, stream.codec, stream.kind, stream.language) for stream in probed.subtitle_streams]
    assert probed.has_video
    assert probed.duration_s == pytest.approx(media.VIDEO_SECONDS, abs=DURATION_TOLERANCE_S)
    assert audio == [(0, "aac", "hun", None, False), (1, "opus", "eng", "Original", True)]
    assert subtitles == [(0, "subrip", "text", "eng"), (1, "ass", "text", "hun")]
    assert [stream.forced for stream in probed.subtitle_streams] == [False, True]


@pytest.mark.parametrize(
    ("encoder", "extension", "codec"),
    [("aac", "m4a", "aac"), ("libopus", "opus", "opus"), ("ac3", "ac3", "ac3"), ("pcm_s16le", "wav", "pcm_s16le")],
)
def test_probe_audio_file(tmp_path: Path, tools: FfmpegMediaTools, encoder: str, extension: str, codec: str) -> None:
    """An audio file has one track of its codec, no video and its length."""
    probed = _probe(tools, media.audio_file(tmp_path, encoder, extension))

    assert [track.codec for track in probed.audio_tracks] == [codec]
    assert (probed.has_video, probed.subtitle_streams) == (False, ())
    assert probed.duration_s == pytest.approx(media.AUDIO_SECONDS, abs=DURATION_TOLERANCE_S)


def test_probe_refuses_a_non_media_file(tmp_path: Path, tools: FfmpegMediaTools) -> None:
    """A file ffprobe cannot read gives `media_tool_failed` with ffprobe's message."""
    text = tmp_path / "notes.mkv"
    text.write_text("not a video", encoding="utf-8")

    with pytest.raises(DomainError) as caught:
        _probe(tools, text)

    assert (caught.value.code, caught.value.details["tool"]) == (ERROR_MEDIA_TOOL_FAILED, "ffprobe")
    assert caught.value.details["stderr"]


@pytest.mark.parametrize("track", [0, 1])
def test_convert_copies_the_track_codec(tmp_path: Path, tools: FfmpegMediaTools, track: int) -> None:
    """Each track becomes its own Matroska audio file with the source codec and nothing else."""
    source = media.multitrack_video(tmp_path)
    source_codec = _probe(tools, source).audio_tracks[track].codec
    output = tmp_path / "out"

    converted = tools.convert(source, track, output / f"item.a{track}", CancelFlag())

    probed = _probe(tools, converted.path)
    assert (converted.path.name, converted.extension, converted.fell_back) == (f"item.a{track}.mka", "mka", False)
    assert [audio.codec for audio in probed.audio_tracks] == [source_codec]
    assert (probed.has_video, probed.subtitle_streams) == (False, ())
    assert sorted(path.name for path in output.iterdir()) == [converted.path.name]


@pytest.mark.parametrize(
    ("encoder", "extension"), [("aac", "m4a"), ("libopus", "opus"), ("ac3", "ac3"), ("pcm_s16le", "wav")]
)
def test_convert_keeps_each_codec(tmp_path: Path, tools: FfmpegMediaTools, encoder: str, extension: str) -> None:
    """AAC, Opus, AC-3 and PCM are stream-copied into Matroska unchanged."""
    source = media.audio_file(tmp_path, encoder, extension)
    codec = _probe(tools, source).audio_tracks[0].codec

    converted = tools.convert(source, 0, tmp_path / "tone", CancelFlag())

    assert converted.extension == "mka"
    assert [track.codec for track in _probe(tools, converted.path).audio_tracks] == [codec]


def test_convert_falls_back_to_flac(tmp_path: Path, tools: FfmpegMediaTools) -> None:
    """A codec the Matroska muxer refuses is encoded to FLAC; no partial file stays."""
    output = tmp_path / "out"

    converted = tools.convert(media.refused_codec_video(tmp_path), 0, output / "item", CancelFlag())

    probed = _probe(tools, converted.path)
    assert (converted.path.name, converted.extension, converted.fell_back) == ("item.flac", "flac", True)
    assert [track.codec for track in probed.audio_tracks] == ["flac"]
    assert probed.duration_s == pytest.approx(media.VIDEO_SECONDS, abs=DURATION_TOLERANCE_S)
    assert [path.name for path in output.iterdir()] == ["item.flac"]


def test_convert_of_a_missing_track_leaves_nothing(tmp_path: Path, tools: FfmpegMediaTools) -> None:
    """When both attempts fail the error has ffmpeg's message and the output folder stays empty."""
    output = tmp_path / "out"

    with pytest.raises(DomainError) as caught:
        tools.convert(media.multitrack_video(tmp_path), 5, output / "item", CancelFlag())

    assert (caught.value.code, caught.value.details["operation"]) == (ERROR_MEDIA_TOOL_FAILED, "convert")
    assert caught.value.details["stderr"]
    assert not list(output.iterdir())


@pytest.mark.parametrize("stream", [0, 1])
def test_extract_gives_valid_srt(tmp_path: Path, tools: FfmpegMediaTools, stream: int) -> None:
    """SubRip and ASS streams come back as SRT with the source cues and their times."""
    srt = tools.extract(media.multitrack_video(tmp_path), stream, CancelFlag())

    cues = SRT_CUE.findall(srt)
    assert [cue[0] for cue in cues] == ["1", "2"]
    assert cues[0][1] == "00:00:00,000"
    assert _cue_texts(srt) == list(media.SRT_TEXTS)


def test_extract_mov_text(tmp_path: Path, tools: FfmpegMediaTools) -> None:
    """An MP4 `mov_text` stream comes back as SRT."""
    srt = tools.extract(media.mov_text_video(tmp_path), 0, CancelFlag())

    assert _cue_texts(srt) == list(media.SRT_TEXTS)


def test_extract_of_a_missing_stream_fails(tmp_path: Path, tools: FfmpegMediaTools) -> None:
    """A stream index the file does not have gives `media_tool_failed`."""
    with pytest.raises(DomainError) as caught:
        tools.extract(media.mov_text_video(tmp_path), 3, CancelFlag())

    assert (caught.value.code, caught.value.details["operation"]) == (ERROR_MEDIA_TOOL_FAILED, "extract")


def _wav_header(data_bytes: int) -> bytes:
    """Return a 16-bit mono PCM WAV header announcing `data_bytes` of samples."""
    byte_rate = STREAM_RATE_HZ * SAMPLE_BYTES
    riff_size = WAV_HEADER_BYTES - RIFF_PREAMBLE_BYTES + data_bytes
    fields = (
        b"RIFF",
        riff_size,
        b"WAVE",
        b"fmt ",
        PCM_FORMAT_BYTES,
        PCM_FORMAT,
        1,
        STREAM_RATE_HZ,
        byte_rate,
        SAMPLE_BYTES,
        SAMPLE_BYTES * BITS_PER_BYTE,
        b"data",
        data_bytes,
    )
    return struct.pack(WAV_HEADER_LAYOUT, *fields)


class EndlessStream:
    """Feeds a FIFO with a WAV header announcing an hour, then silence as fast as it is read.

    ffmpeg reading it opens its output and keeps converting until it is stopped.
    """

    def __init__(self, fifo: Path) -> None:
        """Create the FIFO and start feeding it."""
        os.mkfifo(fifo)
        self.path = fifo
        self._stop = threading.Event()
        # Read-write, so opening does not wait for a reader and the reader never sees end of file.
        self._descriptor = os.open(fifo, os.O_RDWR)
        os.write(self._descriptor, _wav_header(STREAM_RATE_HZ * SAMPLE_BYTES * ANNOUNCED_SECONDS))
        os.set_blocking(self._descriptor, False)
        self._thread = threading.Thread(target=self._feed, daemon=True)
        self._thread.start()

    def close(self) -> None:
        """Stop feeding and close the FIFO."""
        self._stop.set()
        self._thread.join()
        os.close(self._descriptor)

    def _feed(self) -> None:
        chunk = bytes(STREAM_CHUNK_BYTES)
        while not self._stop.is_set():
            try:
                os.write(self._descriptor, chunk)
            except BlockingIOError:
                time.sleep(FEED_PAUSE_S)


@pytest.fixture
def endless_stream(tmp_path: Path) -> Iterator[Path]:
    """Yield a FIFO that never runs out of WAV audio."""
    stream = EndlessStream(tmp_path / "stream.wav")
    try:
        yield stream.path
    finally:
        stream.close()


class OutputWatcher:
    """Waits in a thread for a file to appear, notes it, and then sets the cancel flag if given one."""

    def __init__(self, path: Path, flag: CancelFlag | None = None) -> None:
        """Start watching `path`."""
        self.seen = threading.Event()
        self._path = path
        self._flag = flag
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def join(self) -> None:
        """Wait for the watch to end."""
        self._thread.join()

    def _watch(self) -> None:
        deadline = time.monotonic() + OUTPUT_WAIT_S
        while not self._path.exists() and time.monotonic() < deadline:
            time.sleep(WATCH_INTERVAL_S)
        if self._path.exists():
            self.seen.set()
        if self._flag is not None:
            self._flag.cancel()


@pytest.mark.skipif(sys.platform == "win32", reason="named pipes (mkfifo) are POSIX only")
def test_cancel_kills_and_cleans_up(tmp_path: Path, endless_stream: Path) -> None:
    """Setting the token kills ffmpeg at once, and its partial output is deleted."""
    output = tmp_path / "out"
    partial = output / "item.mka.part"
    flag = CancelFlag()
    watcher = OutputWatcher(partial, flag)

    with pytest.raises(CancelledError):
        FfmpegMediaTools().convert(endless_stream, 0, output / "item", flag)

    watcher.join()
    assert watcher.seen.is_set()
    assert not list(output.iterdir())


@pytest.mark.skipif(sys.platform == "win32", reason="named pipes (mkfifo) are POSIX only")
def test_timeout_kills_and_cleans_up(tmp_path: Path, endless_stream: Path) -> None:
    """A conversion that outlives its timeout is killed, reported, and leaves no file."""
    output = tmp_path / "out"
    tools = FfmpegMediaTools(timeouts=MediaTimeouts(convert_s=ENDLESS_TIMEOUT_S))
    watcher = OutputWatcher(output / "item.mka.part")
    started = time.monotonic()

    with pytest.raises(DomainError) as caught:
        tools.convert(endless_stream, 0, output / "item", CancelFlag())

    elapsed = time.monotonic() - started
    watcher.join()
    assert (caught.value.code, watcher.seen.is_set()) == (ERROR_MEDIA_TOOL_TIMEOUT, True)
    assert ENDLESS_TIMEOUT_S <= elapsed < ENDLESS_TIMEOUT_S + KILL_BUDGET_S
    assert not list(output.iterdir())
