"""File helpers of the settings writes: temp files, durable replace, Windows sharing retries."""

import os
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path

from transcribe_ui.state.constants import (
    SETTINGS_SHARING_RETRY_DELAYS_S,
    SETTINGS_STALE_TEMP_S,
    SETTINGS_TEMP_PREFIX,
    SETTINGS_TEMP_SUFFIX,
)


@contextmanager
def temp_file(folder: Path) -> Iterator[tuple[int, Path]]:
    """Yield a new temp file in the folder (descriptor, path); it is gone when the block ends."""
    folder.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=SETTINGS_TEMP_PREFIX, suffix=SETTINGS_TEMP_SUFFIX, dir=folder)
    temp_path = Path(temp_name)
    try:
        yield descriptor, temp_path
    finally:
        temp_path.unlink(missing_ok=True)


def remove_stale_temp_files(folder: Path) -> None:
    """Remove temp files a crashed write left behind (old enough not to be another writer's)."""
    cutoff = time.time() - SETTINGS_STALE_TEMP_S
    for leftover in folder.glob(f"{SETTINGS_TEMP_PREFIX}*{SETTINGS_TEMP_SUFFIX}"):
        with suppress(OSError):
            if leftover.stat().st_mtime < cutoff:
                leftover.unlink()


def sync_folder(folder: Path) -> None:
    """Make the rename durable (POSIX; Windows cannot open a folder for fsync)."""
    if sys.platform == "win32":
        return
    with ExitStack() as cleanup:
        descriptor = os.open(folder, os.O_RDONLY)
        cleanup.callback(os.close, descriptor)
        os.fsync(descriptor)


def with_sharing_retries[ResultT](action: Callable[[], ResultT]) -> ResultT:
    """Run a file operation, retrying for about 2 s while Windows reports a sharing violation.

    Windows refuses to replace (or open) a file another process has open; elsewhere a
    `PermissionError` is a real refusal and is raised at once.
    """
    for delay_s in SETTINGS_SHARING_RETRY_DELAYS_S:
        try:
            return action()
        except PermissionError:
            if sys.platform != "win32":
                raise
        time.sleep(delay_s)
    return action()
