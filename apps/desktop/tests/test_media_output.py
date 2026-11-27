"""Outputs and limits of the media adapter: partial files, unwritable outputs, indexes, timeouts."""

from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from support.scripted import ScriptedRunner, WritesThen, error_of, exits, probe_starting_at, scripted_tools, which
from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.media.adapter import FfmpegMediaTools, MediaTimeouts
from transcribe_ui.media.constants import ERROR_MEDIA_INDEX_INVALID, ERROR_MEDIA_OUTPUT_UNWRITABLE
from transcribe_ui.media.tools import ToolLocator

if TYPE_CHECKING:
    from collections.abc import Callable

# Output name (without extension) of the conversions.
OUTPUT_NAME = "item"
# Base limits of the timeout test; the probe keeps its own.
PROBE_LIMIT_S = 7.0
BASE_LIMIT_S = 10.0
# Slowest read rate of the timeout test (bytes/s): the 4-byte source then needs 40 s.
SLOW_RATE = 0.1
SCALED_LIMIT_S = 40.0


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


def test_extract_shifts_a_late_start(source: Path) -> None:
    """A container starting well after zero (MPEG-TS) has its start subtracted: no `-copyts`."""
    tools, runner = scripted_tools(probe_starting_at("1.400000"), exits(stdout=b""))

    tools.extract(source, 0, CancelFlag())

    assert "-copyts" not in runner.calls[1]


def test_partial_names_are_unique(source: Path, output: Path) -> None:
    """Each attempt writes its own partial file, so an orphan never writes into a later attempt's."""
    tools, runner = scripted_tools(WritesThen(exits(1)), WritesThen(exits()))

    tools.convert(source, 0, output / OUTPUT_NAME, CancelFlag())
    tools_again, runner_again = scripted_tools(WritesThen(exits()))
    tools_again.convert(source, 0, output / OUTPUT_NAME, CancelFlag())

    partials = [runner.calls[0][-1], runner.calls[1][-1], runner_again.calls[0][-1]]
    assert len(set(partials)) == len(partials)


def test_unwritable_output_folder(source: Path, tmp_path: Path) -> None:
    """An output folder that cannot be made is `media_output_unwritable`, and nothing runs."""
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"")
    tools, runner = scripted_tools()

    error = error_of(partial(tools.convert, source, 0, blocker / "out" / "item", CancelFlag()))

    assert (error.code, runner.calls) == (ERROR_MEDIA_OUTPUT_UNWRITABLE, [])


def test_unwritable_final_name(source: Path, output: Path) -> None:
    """A finished output that cannot be renamed into place is reported; the partial is removed."""
    (output / "item.mka").mkdir(parents=True)
    tools, _ = scripted_tools(WritesThen(exits()))

    error = error_of(partial(tools.convert, source, 0, output / OUTPUT_NAME, CancelFlag()))

    assert error.code == ERROR_MEDIA_OUTPUT_UNWRITABLE
    assert [path.name for path in output.iterdir()] == ["item.mka"]


@pytest.mark.parametrize("operation", ["convert", "extract"])
def test_negative_index_runs_nothing(source: Path, output: Path, operation: str) -> None:
    """A negative track or stream index is refused before any tool runs."""
    tools, runner = scripted_tools()
    call: Callable[[], object] = partial(tools.convert, source, -1, output / OUTPUT_NAME, CancelFlag())
    if operation == "extract":
        call = partial(tools.extract, source, -1, CancelFlag())

    error = error_of(call)

    assert (error.code, runner.calls) == (ERROR_MEDIA_INDEX_INVALID, [])


def test_timeouts_grow_with_the_file(source: Path, output: Path) -> None:
    """A file that takes longer to read than the base limit at the slowest rate gets that long."""
    timeouts = MediaTimeouts(
        probe_s=PROBE_LIMIT_S,
        convert_s=BASE_LIMIT_S,
        extract_s=BASE_LIMIT_S,
        min_bytes_per_s=SLOW_RATE,
    )
    runner = ScriptedRunner([WritesThen(exits()), probe_starting_at("0"), exits()])
    tools = FfmpegMediaTools(locator=ToolLocator(which=which), runner=runner, timeouts=timeouts)

    tools.convert(source, 0, output / OUTPUT_NAME, CancelFlag())
    tools.extract(source, 0, CancelFlag())

    scaled = pytest.approx(SCALED_LIMIT_S)
    assert runner.timeouts == [scaled, PROBE_LIMIT_S, scaled]
