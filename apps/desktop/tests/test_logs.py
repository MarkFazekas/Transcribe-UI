"""Flow tests of the log file (plan m5 M5.2): nothing on stderr, rotation shared between processes."""

import io
import logging
import warnings
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from support.fakes import seeded_ports
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import GetAppInfoRequest, Response
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.api.routes import Route
from transcribe_ui.api.schema import response_adapter
from transcribe_ui.cli.constants import EXIT_ERROR
from transcribe_ui.cli.main import main
from transcribe_ui.state.logs import SharedRotatingFileHandler

CRASH_DETAIL = "detail kept for the log file"
# Small sizes, so a few records rotate.
MAX_BYTES = 200
BACKUP_COUNT = 2
RECORDS = 20
ENCODING = "utf-8"
# Padding that makes each record a fifth of a log file.
PADDING = "x" * (MAX_BYTES // 5)


@pytest.fixture
def log_file(tmp_path: Path) -> Path:
    """The log file of a test (its folder does not exist yet)."""
    return tmp_path / "log" / "test.log"


@pytest.fixture
def log_handler(log_file: Path) -> Iterator[SharedRotatingFileHandler]:
    """A handler with small rotation limits, closed afterwards."""
    # retry_bytes=0: a rotation that fails for a moment (a Windows scanner holding the file) is
    # retried at the next record, so the backups still end up complete.
    file_handler = SharedRotatingFileHandler(log_file, max_bytes=MAX_BYTES, backup_count=BACKUP_COUNT, retry_bytes=0)
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    yield file_handler
    file_handler.close()


def _crash_with_warning(_request: GetAppInfoRequest, _context: RequestContext) -> Response:
    warnings.warn("a library warning", UserWarning, stacklevel=1)
    raise RuntimeError(CRASH_DETAIL)


def _emit(target: logging.Handler, message: str) -> None:
    target.handle(logging.LogRecord("test", logging.INFO, __file__, 1, message, None, None))


def _record_text(index: int) -> str:
    return f"record {index:02d} {PADDING}"


def _messages(log_file: Path) -> list[str]:
    files = [log_file, *sorted(log_file.parent.glob(f"{log_file.name}.*"))]
    return [line for path in files if path.exists() for line in path.read_text(encoding=ENCODING).splitlines()]


def test_cli_logs_go_to_the_log_file_not_stderr(tmp_path: Path, log_file: Path) -> None:
    """Warnings and the traceback of a crash land in the log file; stderr stays empty."""
    request_file = tmp_path / "request.json"
    request_file.write_text('{"action": "get_app_info"}', encoding=ENCODING)
    backend = Backend(seeded_ports(tmp_path), {"get_app_info": Route(run=_crash_with_warning, needs_database=False)})
    stderr = io.BytesIO()
    code = main(
        ["run", str(request_file)],
        make_backend=lambda: backend,
        stdout=io.BytesIO(),
        stderr=stderr,
        log_file=log_file,
    )
    assert code == EXIT_ERROR
    assert not stderr.getvalue()
    log_text = log_file.read_text(encoding=ENCODING)
    assert "a library warning" in log_text
    assert f"RuntimeError: {CRASH_DETAIL}" in log_text


def test_rotation_shifts_the_backups(log_handler: SharedRotatingFileHandler, log_file: Path) -> None:
    """Rotation moves the file to `.1` and shifts older backups; nothing beyond the count is kept."""
    for index in range(RECORDS):
        _emit(log_handler, _record_text(index))
    backups = sorted(path.name for path in log_file.parent.iterdir())
    assert backups == ["test.log", "test.log.1", "test.log.2"]
    assert log_file.read_text(encoding=ENCODING).splitlines()[-1].startswith(f"record {RECORDS - 1:02d}")


def test_rotation_while_another_handle_holds_the_file(log_file: Path) -> None:
    """Another process's open handle never costs a record (Windows refuses the rename then)."""
    # Enough backups that rotation itself never discards a record.
    many_backups = SharedRotatingFileHandler(log_file, max_bytes=MAX_BYTES, backup_count=RECORDS)
    many_backups.setFormatter(logging.Formatter("%(message)s"))
    expected = [_record_text(index) for index in range(RECORDS)]
    with closing(many_backups):
        _emit(many_backups, expected[0])
        with log_file.open("a", encoding=ENCODING):
            for message in expected[1:]:
                _emit(many_backups, message)
    assert sorted(_messages(log_file)) == expected


def test_file_rotated_by_another_process_is_reopened(log_handler: SharedRotatingFileHandler, log_file: Path) -> None:
    """When another process renamed the file away, the next record goes to a new file at the path."""
    _emit(log_handler, "before")
    try:
        log_file.replace(log_file.with_name("test.log.1"))
    except PermissionError:
        pytest.skip("this OS refuses to rename an open file, so no other process can rotate it away")
    _emit(log_handler, "after")
    assert log_file.read_text(encoding=ENCODING).splitlines() == ["after"]


def _refuse_open() -> io.TextIOWrapper:
    msg = "simulated: the log file cannot be opened"
    raise OSError(msg)


def test_failed_reopen_never_raises_and_recovers(
    log_handler: SharedRotatingFileHandler,
    log_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rotation whose reopen fails drops records without raising; later records are written again."""
    monkeypatch.setattr(logging, "raiseExceptions", False)
    _emit(log_handler, _record_text(0))
    monkeypatch.setattr(log_handler, "_open", _refuse_open)
    for index in range(1, RECORDS):
        _emit(log_handler, _record_text(index))
    monkeypatch.undo()
    _emit(log_handler, "recovered")
    assert log_file.read_text(encoding=ENCODING).splitlines()[-1] == "recovered"


def test_records_after_close_are_dropped(log_handler: SharedRotatingFileHandler, log_file: Path) -> None:
    """A closed handler does not reopen its file for late records."""
    _emit(log_handler, "kept")
    log_handler.close()
    _emit(log_handler, "late")
    assert log_file.read_text(encoding=ENCODING).splitlines() == ["kept"]


def test_leftover_rotating_file_becomes_a_backup(log_handler: SharedRotatingFileHandler, log_file: Path) -> None:
    """A `.rotating` file left by an interrupted rotation is kept as a backup, not overwritten."""
    log_file.parent.mkdir(parents=True)
    log_file.with_name("test.log.rotating").write_text("interrupted\n", encoding=ENCODING)
    for index in range(RECORDS // 4):
        _emit(log_handler, _record_text(index))
    assert not log_file.with_name("test.log.rotating").exists()
    assert "interrupted" in _messages(log_file)


def test_cli_answers_json_without_a_log_file(tmp_path: Path) -> None:
    """With an unusable log file (its folder is a file), a crash is still answered with JSON."""
    request_file = tmp_path / "request.json"
    request_file.write_text('{"action": "get_app_info"}', encoding=ENCODING)
    blocker = tmp_path / "not-a-folder"
    blocker.write_bytes(b"")
    backend = Backend(seeded_ports(tmp_path), {"get_app_info": Route(run=_crash_with_warning, needs_database=False)})
    stdout = io.BytesIO()
    stderr = io.BytesIO()
    log_file = blocker / "test.log"
    code = main(
        ["run", str(request_file)], make_backend=lambda: backend, stdout=stdout, stderr=stderr, log_file=log_file
    )
    assert code == EXIT_ERROR
    assert response_adapter().validate_json(stdout.getvalue()).status == "error"
    assert not stderr.getvalue()
