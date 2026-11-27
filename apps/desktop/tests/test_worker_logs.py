"""Worker logs kept in the log dir (plan m5 M5.12): written whole, never over another, never twice."""

import os
from pathlib import Path

import pytest

from transcribe_ui.runs.worker_logs import keep_log, worker_log_path

# A run id.
RUN_ID = "01JDZZZZZZZZZZZZZZZZZZZR01"
# Two different logs.
FIRST_LOG = b"first run\n"
LATER_LOG = b"straggler\n"


def _names(log_dir: Path) -> list[str]:
    return sorted(path.name for path in log_dir.iterdir())


def _fail(*_args: object) -> None:
    msg = "disk full"
    raise OSError(msg)


def test_logs_never_replace_each_other(tmp_path: Path) -> None:
    """A different log goes to the next free number; the same bytes are not written again."""
    keep_log(tmp_path, RUN_ID, FIRST_LOG)
    keep_log(tmp_path, RUN_ID, LATER_LOG)
    keep_log(tmp_path, RUN_ID, LATER_LOG)
    second = tmp_path / f"worker-{RUN_ID}-2.log"
    assert (_names(tmp_path), worker_log_path(tmp_path, RUN_ID).read_bytes(), second.read_bytes()) == (
        [f"worker-{RUN_ID}-2.log", f"worker-{RUN_ID}.log"],
        FIRST_LOG,
        LATER_LOG,
    )


def test_failed_write_leaves_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A write that fails leaves no file behind; the retry keeps the log under the first name."""
    monkeypatch.setattr(os, "fsync", _fail)
    with pytest.raises(OSError, match="disk full"):
        keep_log(tmp_path, RUN_ID, FIRST_LOG)
    assert _names(tmp_path) == []
    monkeypatch.undo()
    keep_log(tmp_path, RUN_ID, FIRST_LOG)
    assert _names(tmp_path) == [f"worker-{RUN_ID}.log"]


def test_crash_leftover_removed(tmp_path: Path) -> None:
    """A temp file a crash left for the run is removed by the next write."""
    leftover = tmp_path / f".worker-{RUN_ID}-crashed.part"
    leftover.write_bytes(b"partial")
    keep_log(tmp_path, RUN_ID, FIRST_LOG)
    assert _names(tmp_path) == [f"worker-{RUN_ID}.log"]
