"""Worker logs kept in the user log dir (`worker-<run_id>.log`), written whole and never over another.

A log is written to a temp file in the log dir first (flushed to disk), then published under its
name with a hard link, which fails instead of replacing an existing file (on POSIX and on
Windows/NTFS alike), and the temp file is removed; a failed write removes it too, and temp files
a crash left for the run are removed before the next write (the runs lock and the writer lock
keep any other writer out). So a kept log is always complete. The first log of a run is
`worker-<run_id>.log`; a log collected again (after a straggler) goes to
`worker-<run_id>-<n>.log` with the next free n from 2. A log already kept with the same bytes
(a collection retried after a later step failed) is not written again.
"""

import itertools
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

from transcribe_ui.runs.constants import (
    FIRST_EXTRA_LOG_NUMBER,
    WORKER_LOG_EXTRA_NAME,
    WORKER_LOG_NAME,
    WORKER_LOG_TEMP_PREFIX,
    WORKER_LOG_TEMP_SUFFIX,
)


def worker_log_path(log_dir: Path, run_id: str) -> Path:
    """Return where a run's (first) worker log is kept."""
    return log_dir / WORKER_LOG_NAME.format(run_id=run_id)


def keep_log(log_dir: Path, run_id: str, body: bytes) -> None:
    """Keep the worker log, unless a log with the same bytes is kept already.

    Raises:
        OSError: The log could not be written (nothing partial is left).
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    prefix = WORKER_LOG_TEMP_PREFIX.format(run_id=run_id)
    for leftover in log_dir.glob(f"{prefix}*{WORKER_LOG_TEMP_SUFFIX}"):
        leftover.unlink(missing_ok=True)
    extra = WORKER_LOG_EXTRA_NAME.format(run_id=run_id, number="*")
    kept = [worker_log_path(log_dir, run_id), *log_dir.glob(extra)]
    if any(_same_bytes(path, body) for path in kept):
        return
    staged = _written_temp(log_dir, prefix, body)
    try:
        _publish(staged, log_dir, run_id)
    except OSError:
        staged.unlink(missing_ok=True)
        raise
    staged.unlink(missing_ok=True)


def _same_bytes(path: Path, body: bytes) -> bool:
    return path.is_file() and path.read_bytes() == body


def _written_temp(log_dir: Path, prefix: str, body: bytes) -> Path:
    """Write the body to a new temp file in the log dir, flushed to disk; return its path.

    Raises:
        OSError: The write failed (the temp file is removed).
    """
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=WORKER_LOG_TEMP_SUFFIX, dir=log_dir)
    staged = Path(name)
    try:
        _write_all(descriptor, body)
    except OSError:
        staged.unlink(missing_ok=True)
        raise
    return staged


def _write_all(descriptor: int, body: bytes) -> None:
    with os.fdopen(descriptor, "wb") as written:
        written.write(body)
        written.flush()
        os.fsync(written.fileno())


def _publish(staged: Path, log_dir: Path, run_id: str) -> None:
    """Link the temp file under the first free log name (a link never replaces a file).

    Raises:
        OSError: The link failed for another reason than a taken name.
    """
    for target in _log_names(log_dir, run_id):
        try:
            os.link(staged, target)
        except FileExistsError:
            continue
        return


def _log_names(log_dir: Path, run_id: str) -> Iterator[Path]:
    """Yield the log names of the run in order: `worker-<run_id>.log`, then `-2`, `-3`, ..."""
    yield worker_log_path(log_dir, run_id)
    for number in itertools.count(FIRST_EXTRA_LOG_NUMBER):
        yield log_dir / WORKER_LOG_EXTRA_NAME.format(run_id=run_id, number=number)
