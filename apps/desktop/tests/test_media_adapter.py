"""The media adapter with a scripted process runner: commands, parsing, errors, cleanup (plan m5 M5.4).

No ffmpeg runs here; `test_media_ffmpeg.py` covers the real tools and `test_media_runner.py` the
real process handling.
"""

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import pytest

from transcribe_ui.api.errors import CancelledError, DomainError
from transcribe_ui.api.ports import CancelFlag, CancelToken
from transcribe_ui.media.adapter import FfmpegMediaTools
from transcribe_ui.media.classes import Tool
from transcribe_ui.media.constants import (
    ERROR_MEDIA_TOOL_FAILED,
    ERROR_MEDIA_TOOL_NOT_FOUND,
    ERROR_MEDIA_TOOL_TIMEOUT,
    FFMPEG,
    FFPROBE,
    PROBE_UNREADABLE_MESSAGE,
    STDERR_TAIL_CHARS,
)
from transcribe_ui.media.runner import ProcessResult, ProcessTimeoutError
from transcribe_ui.media.tools import ToolLocator, ToolOverrides, bundle_dir, executable_name

# One scripted process: gets the argv, returns the result or raises.
type Step = Callable[[Sequence[str]], ProcessResult]

# What the scripted `which` finds on PATH.
PATH_TOOL = "/usr/bin/{tool}"
# Folder name of the fake bundle.
BUNDLE = "bundle"
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


@dataclass
class ScriptedRunner:
    """`ProcessRunner` that plays one step per call and records each argv."""

    steps: list[Step]
    calls: list[list[str]] = field(default_factory=list)

    def run(self, argv: Sequence[str], timeout_s: float, cancel: CancelToken) -> ProcessResult:
        """Play the next step."""
        assert (timeout_s > 0, cancel.is_cancelled()) == (True, False)
        self.calls.append(list(argv))
        return self.steps.pop(0)(argv)


@dataclass(frozen=True)
class WritesThen:
    """Step that writes the output named last in argv (as ffmpeg starts to), then plays `after`."""

    after: Step

    def __call__(self, argv: Sequence[str]) -> ProcessResult:
        """Write the output, then play the next step."""
        Path(argv[-1].removeprefix("file:")).write_bytes(b"partial")
        return self.after(argv)


@dataclass(frozen=True)
class Raises:
    """Step that raises (a timeout, a cancel, an interrupt, a start failure)."""

    error: BaseException

    def __call__(self, _argv: Sequence[str]) -> ProcessResult:
        """Raise the error.

        Raises:
            BaseException: Always, the given error.
        """
        raise self.error


def _exits(code: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> Step:
    return lambda _argv: ProcessResult(code, stdout, stderr)


def _which(tool: str) -> str:
    """Return what the scripted PATH holds, spelled as this OS spells paths."""
    return str(Path(PATH_TOOL.format(tool=tool)))


def _tools(*steps: Step) -> tuple[FfmpegMediaTools, ScriptedRunner]:
    runner = ScriptedRunner(list(steps))
    return FfmpegMediaTools(locator=ToolLocator(which=_which), runner=runner), runner


def _error_of(call: Callable[[], object]) -> DomainError:
    try:
        call()
    except DomainError as error:
        return error
    pytest.fail("no DomainError was raised")


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
    tools, runner = _tools(_exits(stdout=PROBE_DOCUMENT))

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
    assert (runner.calls[0][0], runner.calls[0][-1]) == (_which(FFPROBE), f"file:{source.absolute()}")


def test_probe_without_any_duration(source: Path) -> None:
    """No container or stream duration gives None, not zero."""
    tools, _ = _tools(_exits(stdout=b'{"streams": [{"codec_type": "audio", "codec_name": "mp3"}]}'))

    assert tools.probe(source, CancelFlag()).duration_s is None


def test_probe_with_unreadable_output(source: Path) -> None:
    """Output that is not ffprobe's JSON, even with exit code 0, is a tool failure."""
    tools, _ = _tools(_exits(stdout=b"not json"))

    error = _error_of(partial(tools.probe, source, CancelFlag()))

    assert (error.code, error.message) == (ERROR_MEDIA_TOOL_FAILED, PROBE_UNREADABLE_MESSAGE)


def test_failure_keeps_only_the_end_of_stderr(source: Path) -> None:
    """A long stderr is cut to its end, where ffmpeg puts the reason."""
    stderr = f"{'x' * LONG_STDERR_CHARS}the reason".encode()
    tools, _ = _tools(_exits(1, stderr=stderr))

    error = _error_of(partial(tools.probe, source, CancelFlag()))

    kept = str(error.details["stderr"])
    assert (error.code, error.details["exit_code"], error.details["tool"]) == (ERROR_MEDIA_TOOL_FAILED, 1, FFPROBE)
    assert (len(kept), kept.endswith("the reason")) == (STDERR_TAIL_CHARS, True)


def test_convert_command(source: Path, output: Path) -> None:
    """Stream copy of the chosen track, audio only, no metadata, into a partial Matroska file."""
    tools, runner = _tools(WritesThen(_exits()))

    converted = tools.convert(source, 1, output / "item.a1", CancelFlag())

    argv = runner.calls[0]
    assert (converted.path, converted.extension, converted.fell_back) == (output / "item.a1.mka", "mka", False)
    assert (argv[0], argv[argv.index("-map") + 1], argv[argv.index("-c") + 1]) == (_which(FFMPEG), "0:a:1", "copy")
    assert {"-vn", "-sn", "-dn", "-nostdin"} <= set(argv)
    assert argv[-3:] == ["-f", "matroska", f"file:{converted.path.absolute()}.part"]
    assert converted.path.read_bytes() == b"partial"


def test_convert_falls_back_to_flac(source: Path, output: Path) -> None:
    """A refused stream copy leaves no partial file; the FLAC attempt then writes the output."""
    tools, runner = _tools(WritesThen(_exits(1, stderr=b"Could not write header")), WritesThen(_exits()))

    converted = tools.convert(source, 0, output / "item", CancelFlag())

    flac_argv = runner.calls[1]
    assert (converted.extension, converted.fell_back) == ("flac", True)
    assert flac_argv[flac_argv.index("-c:a") + 1] == "flac"
    assert sorted(path.name for path in output.iterdir()) == ["item.flac"]


def test_convert_failing_twice_leaves_nothing(source: Path, output: Path) -> None:
    """Both attempts failing is one error with the FLAC attempt's message; no file remains."""
    failure = WritesThen(_exits(1, stderr=b"broken input"))
    tools, _ = _tools(failure, failure)

    error = _error_of(partial(tools.convert, source, 0, output / "item", CancelFlag()))

    assert (error.code, error.details["stderr"]) == (ERROR_MEDIA_TOOL_FAILED, "broken input")
    assert not list(output.iterdir())


@pytest.mark.parametrize(
    ("raised", "code"),
    [(ProcessTimeoutError(), ERROR_MEDIA_TOOL_TIMEOUT), (CancelledError(), "cancelled")],
)
def test_convert_stopped_leaves_nothing(source: Path, output: Path, raised: BaseException, code: str) -> None:
    """A timeout or cancel deletes the partial file and skips the FLAC attempt."""
    tools, runner = _tools(WritesThen(Raises(raised)))

    error = _error_of(partial(tools.convert, source, 0, output / "item", CancelFlag()))

    assert (error.code, len(runner.calls)) == (code, 1)
    assert not list(output.iterdir())


def test_convert_interrupted_leaves_nothing(source: Path, output: Path) -> None:
    """A KeyboardInterrupt (second Ctrl+C) also removes the partial file on its way out."""
    tools, _ = _tools(WritesThen(Raises(KeyboardInterrupt())))

    with pytest.raises(KeyboardInterrupt):
        tools.convert(source, 0, output / "item", CancelFlag())

    assert not list(output.iterdir())


def test_extract_command_and_text(source: Path) -> None:
    """Extraction keeps the container's timestamps, writes SRT to stdout and decodes it as UTF-8."""
    srt = "1\n00:00:00,000 --> 00:00:01,000\nÁrvíztűrő\n\n\udcff\n".encode(errors="surrogateescape")
    tools, runner = _tools(_exits(stdout=srt))

    text = tools.extract(source, 2, CancelFlag())

    argv = runner.calls[0]
    assert text == "1\n00:00:00,000 --> 00:00:01,000\nÁrvíztűrő\n\n�\n"
    assert ("-copyts" in argv, argv[argv.index("-map") + 1]) == (True, "0:s:2")
    assert argv[-5:] == ["-c:s", "srt", "-f", "srt", "pipe:1"]


def test_tool_that_cannot_start(source: Path) -> None:
    """An OSError from starting the process is `media_tool_not_found`."""
    tools, _ = _tools(Raises(PermissionError(PERMISSION_DENIED, "Permission denied")))

    error = _error_of(partial(tools.probe, source, CancelFlag()))

    assert (error.code, error.details["source"]) == (ERROR_MEDIA_TOOL_NOT_FOUND, "start")
    assert "Permission denied" in error.message


def _make_tool(folder: Path, tool: Tool) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / executable_name(tool)
    path.write_bytes(b"")
    return path


def test_override_comes_first(tmp_path: Path) -> None:
    """The settings path wins over the bundle and PATH, per tool."""
    override = _make_tool(tmp_path / "custom", FFMPEG)
    _make_tool(tmp_path / BUNDLE, FFMPEG)
    locator = ToolLocator(ToolOverrides(ffmpeg=override), bundle_dir=tmp_path / BUNDLE, which=_which)

    assert (locator.resolve(FFMPEG), locator.resolve(FFPROBE)) == (override, Path(_which(FFPROBE)))


def test_missing_override_is_an_error(tmp_path: Path) -> None:
    """A wrong settings path is reported, never silently replaced by another ffprobe."""
    _make_tool(tmp_path / BUNDLE, FFPROBE)
    locator = ToolLocator(ToolOverrides(ffprobe=tmp_path / "nowhere"), bundle_dir=tmp_path / BUNDLE, which=_which)

    error = _error_of(partial(locator.resolve, FFPROBE))

    assert (error.code, error.details["source"]) == (ERROR_MEDIA_TOOL_NOT_FOUND, "settings")


def test_bundle_comes_before_path(tmp_path: Path) -> None:
    """The bundled build is used even when PATH has another one; a tool it lacks comes from PATH."""
    bundled = _make_tool(tmp_path / BUNDLE, FFMPEG)
    locator = ToolLocator(bundle_dir=tmp_path / BUNDLE, which=_which)

    assert (locator.resolve(FFMPEG), locator.resolve(FFPROBE)) == (bundled, Path(_which(FFPROBE)))


def test_nothing_found(tmp_path: Path) -> None:
    """Without override, bundle or PATH entry the error says where to set it."""
    locator = ToolLocator(bundle_dir=tmp_path, which=lambda _tool: None)

    error = _error_of(partial(locator.resolve, FFMPEG))

    assert (error.code, error.details["source"]) == (ERROR_MEDIA_TOOL_NOT_FOUND, "search")
    assert "settings" in error.message


def test_tool_not_found_runs_nothing(source: Path) -> None:
    """The runner is not called when the tool cannot be resolved."""
    runner = ScriptedRunner([])
    tools = FfmpegMediaTools(locator=ToolLocator(which=lambda _tool: None), runner=runner)

    _error_of(partial(tools.extract, source, 0, CancelFlag()))

    assert not runner.calls


def test_bundle_dir_only_when_frozen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A PyInstaller build's unpack folder is the bundle; a normal run has none."""
    assert bundle_dir() is None
    monkeypatch.setattr(sys, "frozen", value=True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert bundle_dir() == tmp_path
