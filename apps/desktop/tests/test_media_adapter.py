"""The media adapter with a scripted process runner: commands, parsing, errors, cleanup (plan m5 M5.4).

No ffmpeg runs here; `test_media_ffmpeg.py` covers the real tools, `test_media_runner.py` and
`test_media_kill.py` the real process handling, `test_media_output.py` outputs and limits.
"""

from functools import partial
from pathlib import Path

import pytest

from support.scripted import Raises, WritesThen, error_of, exits, probe_starting_at, scripted_tools, which
from transcribe_ui.api.errors import CancelledError
from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.media.constants import (
    ERROR_MEDIA_TOOL_FAILED,
    ERROR_MEDIA_TOOL_NOT_FOUND,
    ERROR_MEDIA_TOOL_TIMEOUT,
    FFMPEG,
    FFPROBE,
    PROBE_UNREADABLE_MESSAGE,
    STDERR_TAIL_CHARS,
)
from transcribe_ui.media.runner import ProcessTimeoutError

# A probe document with the corner cases: cover art, tag case, `und`, blank title, no codec name,
# bitmap and unknown subtitle codecs, a data stream, no container duration.
PROBE_DOCUMENT = b"""{
  "streams": [
    {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
    {"codec_type": "audio", "codec_name": "aac", "channels": 2, "tags": {"LANGUAGE": "HUN", "title": " "}},
    {"codec_type": "audio", "tags": {"language": "und"}, "disposition": {"default": 1}, "duration": "12.5"},
    {"codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle", "tags": {"language": "eng"}},
    {"codec_type": "subtitle", "codec_name": "dvd_subtitle"},
    {"codec_type": "subtitle", "codec_name": "eia_608"},
    {"codec_type": "subtitle", "codec_name": "subrip", "disposition": {"forced": 1}, "tags": {"title": "Signs"}},
    {"codec_type": "data", "codec_name": "bin_data"}
  ],
  "format": {"duration": "N/A"}
}"""
# Duration of the longest stream in PROBE_DOCUMENT.
PROBED_DURATION_S = 12.5
# Length of the long stderr of the truncation test.
LONG_STDERR_CHARS = 2000
# errno of "permission denied".
PERMISSION_DENIED = 13


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """Return a (fake) media file."""
    path = tmp_path / "film.mkv"
    path.write_bytes(b"film")
    return path


@pytest.fixture
def output(tmp_path: Path) -> Path:
    """Return the output folder (created by the conversion)."""
    return tmp_path / "out"


def test_probe_parses_ffprobe_json(source: Path) -> None:
    """Cover art is not video; tags in any case; `und` and blank are none; codecs are classified."""
    tools, runner = scripted_tools(exits(stdout=PROBE_DOCUMENT))

    probed = tools.probe(source, CancelFlag())

    audio = [(track.codec, track.language, track.title, track.default, track.channels) for track in probed.audio_tracks]
    subtitles = [(stream.codec, stream.kind, stream.language, stream.forced) for stream in probed.subtitle_streams]
    assert (probed.has_video, probed.duration_s) == (False, PROBED_DURATION_S)
    assert audio == [("aac", "hun", None, False, 2), ("unknown", None, None, True, None)]
    assert subtitles == [
        ("hdmv_pgs_subtitle", "bitmap", "eng", False),
        ("dvd_subtitle", "bitmap", None, False),
        ("eia_608", "other", None, False),
        ("subrip", "text", None, True),
    ]
    assert [stream.index for stream in probed.subtitle_streams] == [0, 1, 2, 3]
    assert (runner.calls[0][0], runner.calls[0][-1]) == (which(FFPROBE), f"file:{source.absolute()}")


def test_probe_without_any_duration(source: Path) -> None:
    """No container or stream duration gives None, not zero."""
    tools, _ = scripted_tools(exits(stdout=b'{"streams": [{"codec_type": "audio", "codec_name": "mp3"}]}'))

    assert tools.probe(source, CancelFlag()).duration_s is None


def test_probe_with_unreadable_output(source: Path) -> None:
    """Output that is not ffprobe's JSON, even with exit code 0, is a tool failure."""
    tools, _ = scripted_tools(exits(stdout=b"not json"))

    error = error_of(partial(tools.probe, source, CancelFlag()))

    assert (error.code, error.message) == (ERROR_MEDIA_TOOL_FAILED, PROBE_UNREADABLE_MESSAGE)


def test_failure_keeps_only_the_end_of_stderr(source: Path) -> None:
    """A long stderr is cut to its end, where ffmpeg puts the reason."""
    stderr = f"{'x' * LONG_STDERR_CHARS}the reason".encode()
    tools, _ = scripted_tools(exits(1, stderr=stderr))

    error = error_of(partial(tools.probe, source, CancelFlag()))

    kept = str(error.details["stderr"])
    assert (error.code, error.details["exit_code"], error.details["tool"]) == (ERROR_MEDIA_TOOL_FAILED, 1, FFPROBE)
    assert (len(kept), kept.endswith("the reason")) == (STDERR_TAIL_CHARS, True)


def test_convert_command(source: Path, output: Path) -> None:
    """Stream copy of the chosen track, audio only, no metadata, into a partial Matroska file."""
    tools, runner = scripted_tools(WritesThen(exits()))

    converted = tools.convert(source, 1, output / "item.a1", CancelFlag())

    argv = runner.calls[0]
    assert (converted.path, converted.extension, converted.fell_back) == (output / "item.a1.mka", "mka", False)
    assert (argv[0], argv[argv.index("-map") + 1], argv[argv.index("-c") + 1]) == (which(FFMPEG), "0:a:1", "copy")
    assert {"-vn", "-sn", "-dn", "-nostdin"} <= set(argv)
    partial = argv[-1].removeprefix(f"file:{converted.path.absolute()}.")
    assert (argv[-3:-1], partial.endswith(".part"), len(partial) > len(".part")) == (["-f", "matroska"], True, True)
    assert converted.path.read_bytes() == b"partial"


def test_convert_falls_back_to_flac(source: Path, output: Path) -> None:
    """A refused stream copy leaves no partial file; the FLAC attempt then writes the output."""
    tools, runner = scripted_tools(WritesThen(exits(1, stderr=b"Could not write header")), WritesThen(exits()))

    converted = tools.convert(source, 0, output / "item", CancelFlag())

    flac_argv = runner.calls[1]
    assert (converted.extension, converted.fell_back) == ("flac", True)
    assert flac_argv[flac_argv.index("-c:a") + 1] == "flac"
    assert sorted(path.name for path in output.iterdir()) == ["item.flac"]


def test_convert_failing_twice_leaves_nothing(source: Path, output: Path) -> None:
    """Both attempts failing is one error with the FLAC attempt's message; no file remains."""
    failure = WritesThen(exits(1, stderr=b"broken input"))
    tools, _ = scripted_tools(failure, failure)

    error = error_of(partial(tools.convert, source, 0, output / "item", CancelFlag()))

    assert (error.code, error.details["stderr"]) == (ERROR_MEDIA_TOOL_FAILED, "broken input")
    assert not list(output.iterdir())


@pytest.mark.parametrize(
    ("raised", "code"),
    [(ProcessTimeoutError(), ERROR_MEDIA_TOOL_TIMEOUT), (CancelledError(), "cancelled")],
)
def test_convert_stopped_leaves_nothing(source: Path, output: Path, raised: BaseException, code: str) -> None:
    """A timeout or cancel deletes the partial file and skips the FLAC attempt."""
    tools, runner = scripted_tools(WritesThen(Raises(raised)))

    error = error_of(partial(tools.convert, source, 0, output / "item", CancelFlag()))

    assert (error.code, len(runner.calls)) == (code, 1)
    assert not list(output.iterdir())


def test_convert_interrupted_leaves_nothing(source: Path, output: Path) -> None:
    """A KeyboardInterrupt (second Ctrl+C) also removes the partial file on its way out."""
    tools, _ = scripted_tools(WritesThen(Raises(KeyboardInterrupt())))

    with pytest.raises(KeyboardInterrupt):
        tools.convert(source, 0, output / "item", CancelFlag())

    assert not list(output.iterdir())


def test_extract_command_and_text(source: Path) -> None:
    """Extraction keeps the container's timestamps, writes SRT to stdout and decodes it as UTF-8."""
    srt = "1\n00:00:00,000 --> 00:00:01,000\nÁrvíztűrő\n\n\udcff\n".encode(errors="surrogateescape")
    tools, runner = scripted_tools(probe_starting_at("-0.023000"), exits(stdout=srt))

    text = tools.extract(source, 2, CancelFlag())

    argv = runner.calls[1]
    assert text == "1\n00:00:00,000 --> 00:00:01,000\nÁrvíztűrő\n\n�\n"
    assert ("-copyts" in argv, argv[argv.index("-map") + 1]) == (True, "0:s:2")
    assert argv[-5:] == ["-c:s", "srt", "-f", "srt", "pipe:1"]


def test_tool_that_cannot_start(source: Path) -> None:
    """An OSError from starting the process is `media_tool_not_found`."""
    tools, _ = scripted_tools(Raises(PermissionError(PERMISSION_DENIED, "Permission denied")))

    error = error_of(partial(tools.probe, source, CancelFlag()))

    assert (error.code, error.details["source"]) == (ERROR_MEDIA_TOOL_NOT_FOUND, "start")
    assert "Permission denied" in error.message
