"""Worker logs (plan m5 M5.12): without hard links, and with temp files another program holds open."""

import os
from pathlib import Path

import pytest

from transcribe_ui.runs.worker_logs import keep_log, worker_log_path

# A run id.
RUN_ID = "01JDZZZZZZZZZZZZZZZZZZZR01"
# Two different logs.
FIRST_LOG = b"first run\n"
LATER_LOG = b"straggler\n"
# The real `Path.unlink` (tests replace it).
UNLINK = Path.unlink


def _names(log_dir: Path) -> list[str]:
    return sorted(path.name for path in log_dir.iterdir())


def _fail(*_args: object) -> None:
    msg = "disk full"
    raise OSError(msg)


def _no_hard_links(*_args: object) -> None:
    msg = "hard links are not supported here"
    raise PermissionError(msg)


def test_without_hard_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without hard links the log is published without replacing: numbered, whole, written once."""
    monkeypatch.setattr(os, "link", _no_hard_links)
    keep_log(tmp_path, RUN_ID, FIRST_LOG)
    keep_log(tmp_path, RUN_ID, LATER_LOG)
    keep_log(tmp_path, RUN_ID, LATER_LOG)
    second = tmp_path / f"worker-{RUN_ID}-2.log"
    assert (_names(tmp_path), worker_log_path(tmp_path, RUN_ID).read_bytes(), second.read_bytes()) == (
        [f"worker-{RUN_ID}-2.log", f"worker-{RUN_ID}.log"],
        FIRST_LOG,
        LATER_LOG,
    )


def test_stuck_leftover_does_not_block(tmp_path: Path) -> None:
    """A leftover that cannot be removed (here a folder) is only logged; the log is still kept."""
    stuck = tmp_path / f".worker-{RUN_ID}-stuck.part"
    stuck.mkdir()
    keep_log(tmp_path, RUN_ID, FIRST_LOG)
    assert _names(tmp_path) == [stuck.name, f"worker-{RUN_ID}.log"]


def _unlink_but_temp_files(path: Path, *, missing_ok: bool = False) -> None:
    """`Path.unlink`, except that a temp file is held open by another program (Windows AV, indexer)."""
    if path.suffix == ".part":
        msg = "held open by another program"
        raise PermissionError(msg)
    UNLINK(path, missing_ok=missing_ok)


def test_temp_file_kept_open_after_publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The temp file cannot be removed after publishing: the log is kept anyway; the next write removes it."""
    monkeypatch.setattr(Path, "unlink", _unlink_but_temp_files)
    keep_log(tmp_path, RUN_ID, FIRST_LOG)
    monkeypatch.undo()
    assert [name.endswith(".part") for name in _names(tmp_path)] == [True, False]
    keep_log(tmp_path, RUN_ID, FIRST_LOG)
    assert _names(tmp_path) == [f"worker-{RUN_ID}.log"]
