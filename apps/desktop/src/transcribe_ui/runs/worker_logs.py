"""Worker logs kept in the user log dir (`worker-<run_id>.log`), written whole and never over another.

A log is written to a temp file in the log dir first (flushed to disk), then published under its
name without replacing anything:

- with a hard link, which fails on a taken name (POSIX and Windows/NTFS alike);
- on a file system without hard links (exFAT, FAT32, some network or FUSE mounts): on Windows by
  renaming the temp file (Windows never renames over an existing file); on POSIX by creating the
  name exclusively (`O_CREAT | O_EXCL`) and writing it, removed again if the write fails.

On POSIX the log dir is then synced, so the log's entry is durable before the run's objects are
deleted from R2 (best effort: some file systems cannot sync a folder). The temp file is removed
after publishing and after a failure, and temp files a crash left for the run are removed before
the next write (the runs lock and the writer lock keep any other writer out); each removal is best
effort and only logged, so an antivirus or indexer handle on Windows never blocks a collection.
The first log of a run is `worker-<run_id>.log`; a log collected again (after a straggler) goes to
`worker-<run_id>-<n>.log` with the next free n from 2. A log already kept with the same bytes (a
collection retried after a later step failed) is not written again.
"""

import itertools
import logging
import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

from transcribe_ui.runs.constants import (
    FIRST_EXTRA_LOG_NUMBER,
    WORKER_LOG_CREATE_FLAGS,
    WORKER_LOG_EXTRA_NAME,
    WORKER_LOG_FILE_MODE,
    WORKER_LOG_NAME,
    WORKER_LOG_TEMP_PREFIX,
    WORKER_LOG_TEMP_SUFFIX,
)
from transcribe_ui.state.files import sync_folder

logger = logging.getLogger(__name__)


def worker_log_path(log_dir: Path, run_id: str) -> Path:
    """Return where a run's (first) worker log is kept."""
    return log_dir / WORKER_LOG_NAME.format(run_id=run_id)


def keep_log(log_dir: Path, run_id: str, body: bytes) -> None:
    """Keep the worker log, unless a log with the same bytes is kept already.

    Raises:
        OSError: The log could not be written (nothing partial is left under a log name).
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    prefix = WORKER_LOG_TEMP_PREFIX.format(run_id=run_id)
    for leftover in log_dir.glob(f"{prefix}*{WORKER_LOG_TEMP_SUFFIX}"):
        _discard(leftover)
    extra = WORKER_LOG_EXTRA_NAME.format(run_id=run_id, number="*")
    kept = [worker_log_path(log_dir, run_id), *log_dir.glob(extra)]
    if any(_same_bytes(path, body) for path in kept):
        return
    staged = _written_temp(log_dir, prefix, body)
    try:
        _publish(staged, body, log_dir, run_id)
    except OSError:
        _discard(staged)
        raise
    _discard(staged)
    _sync(log_dir)


def _same_bytes(path: Path, body: bytes) -> bool:
    return path.is_file() and path.read_bytes() == body


def _discard(path: Path) -> None:
    """Remove a temp file if it is there; a failure is only logged (the next write tries again)."""
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        logger.warning("The temp file %s could not be removed (%s)", path.name, type(error).__name__)


def _sync(log_dir: Path) -> None:
    """Make the new log's folder entry durable (POSIX; best effort where a folder cannot be synced)."""
    try:
        sync_folder(log_dir)
    except OSError as error:
        logger.warning("The log folder could not be synced (%s)", type(error).__name__)


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
        _discard(staged)
        raise
    return staged


def _write_all(descriptor: int, body: bytes) -> None:
    with os.fdopen(descriptor, "wb") as written:
        written.write(body)
        written.flush()
        os.fsync(written.fileno())


def _publish(staged: Path, body: bytes, log_dir: Path, run_id: str) -> None:
    """Publish the temp file under the first free log name, never replacing a file.

    Raises:
        OSError: Publishing failed for another reason than a taken name.
    """
    linking = True
    for target in _log_names(log_dir, run_id):
        published = _link(staged, target) if linking else None
        if published is None:
            linking = False  # no hard links on this file system
            published = _create(staged, body, target)
        if published:
            return


def _link(staged: Path, target: Path) -> bool | None:
    """Hard-link the temp file as `target`; False when the name is taken, None without hard links."""
    try:
        os.link(staged, target)
    except FileExistsError:
        return False
    except OSError as error:
        logger.info("Hard links are not available for the worker logs (%s)", type(error).__name__)
        return None
    return True


def _create(staged: Path, body: bytes, target: Path) -> bool:
    """Publish without a hard link and without replacing; return False when the name is taken.

    Raises:
        OSError: The rename, or the exclusive create or its write, failed (nothing is left at `target`).
    """
    if sys.platform == "win32":
        try:
            staged.rename(target)  # Windows never renames over an existing file
        except FileExistsError:
            return False
        return True
    try:
        descriptor = os.open(target, WORKER_LOG_CREATE_FLAGS, WORKER_LOG_FILE_MODE)
    except FileExistsError:
        return False
    try:
        _write_all(descriptor, body)
    except OSError:
        target.unlink(missing_ok=True)
        raise
    return True


def _log_names(log_dir: Path, run_id: str) -> Iterator[Path]:
    """Yield the log names of the run in order: `worker-<run_id>.log`, then `-2`, `-3`, ..."""
    yield worker_log_path(log_dir, run_id)
    for number in itertools.count(FIRST_EXTRA_LOG_NUMBER):
        yield log_dir / WORKER_LOG_EXTRA_NAME.format(run_id=run_id, number=number)
