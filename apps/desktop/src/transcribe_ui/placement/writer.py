"""Writing files into place through a temp file in the target's folder (architecture §3.5).

An interrupted write never leaves a partial target. The bytes go to a new temp file next to the
target, `<name>.<token>.tmp` with a random token (the planner's name and path limits keep room for
the tail), created exclusively and without following links, so a planted link or hard link at that
name is never written through, and two downloads of one target never share a temp file. The file
is flushed to disk and then moved:

- a new file without replacing anything: a hard link plus removing the temp file on POSIX,
  `os.rename` on Windows; an existing target makes the move fail, and the file is skipped as
  existing. Where the file system has no hard links, the target is checked and replaced (a target
  created in between is then replaced: accepted);
- an overwrite with `os.replace`, which replaces an existing file on Windows too.

On Windows a move refused for a sharing violation (an antivirus or an indexer holding the file) is
retried for about 2 s. The folder is synced after the move on POSIX. A failed write removes its
temp file; temp files of the target that a killed download left behind are removed once they are
`STALE_TEMP_S` old.
"""

import glob
import os
import secrets
import stat
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from functools import partial
from pathlib import Path

from transcribe_ui.placement.classes import PlacementOutcome
from transcribe_ui.placement.constants import STALE_TEMP_S, TEMP_FLAGS, TEMP_MODE, TEMP_SUFFIX, TEMP_TOKEN_LENGTH
from transcribe_ui.state.files import sync_folder, with_sharing_retries


def write_atomically(target: Path, body: bytes) -> None:
    """Write the bytes to the target through a temp file, replacing an existing target.

    Raises:
        OSError: The temp file could not be written or moved (it is removed).
    """
    staged = _staged(target, body)
    _settled(staged, partial(with_sharing_retries, partial(staged.replace, target)))
    sync_folder(target.parent)


def check_target(target: Path, *, overwrite: bool, next_to_media: bool) -> PlacementOutcome | None:
    """Return the skip a target needs now (missing original folder, existing file), or None to write it."""
    if next_to_media and not target.parent.is_dir():
        return "skip_folder_missing"
    if not overwrite and _exists(target):
        return "skip_existing"
    return None


def place_file(target: Path, body: bytes, *, overwrite: bool, next_to_media: bool) -> PlacementOutcome:
    """Write a subtitle to its target as the plan says, checking the target again first.

    Next to the media the folder must still exist (it is never created there: a missing original
    folder is skipped); the other strategies create their folders. A target that exists by now is
    skipped unless overwriting. Returns what was done.

    Raises:
        OSError: A folder could not be created or the file could not be written.
    """
    skip = check_target(target, overwrite=overwrite, next_to_media=next_to_media)
    if skip is not None:
        return skip
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = _exists(target)
    _remove_stale_temps(target)
    staged = _staged(target, body)
    moved = _settled(staged, lambda: _move(staged, target, overwrite=overwrite))
    sync_folder(target.parent)
    if not moved:
        return "skip_existing"
    return "overwrite" if existed else "place"


def _settled[ResultT](staged: Path, move: Callable[[], ResultT]) -> ResultT:
    """Run the move of a temp file, then remove the temp file if it is still there (a link leaves it)."""
    try:
        moved = move()
    except BaseException:
        _remove_quietly(staged)
        raise
    _remove_quietly(staged)
    return moved


def _token() -> str:
    return secrets.token_hex(TEMP_TOKEN_LENGTH // 2)


def _temp_path(target: Path) -> Path:
    return target.with_name(f"{target.name}.{_token()}{TEMP_SUFFIX}")


def _staged(target: Path, body: bytes) -> Path:
    """Write the bytes to a new temp file next to the target and flush them to disk; return its path."""
    staged = _temp_path(target)
    descriptor = os.open(staged, TEMP_FLAGS, TEMP_MODE)
    try:
        _fill(descriptor, body)
    except BaseException:
        os.close(descriptor)
        _remove_quietly(staged)
        raise
    os.close(descriptor)
    return staged


def _fill(descriptor: int, body: bytes) -> None:
    remaining = body
    while remaining:
        written = os.write(descriptor, remaining)
        remaining = remaining[written:]
    os.fsync(descriptor)


def _move(staged: Path, target: Path, *, overwrite: bool) -> bool:
    """Move the temp file onto the target; return False when a new file found the target taken."""
    if overwrite:
        with_sharing_retries(partial(staged.replace, target))
        return True
    try:
        _move_new(staged, target)
    except FileExistsError:
        return False
    return True


def _move_new(staged: Path, target: Path) -> None:
    """Move the temp file to a target that must not exist.

    Raises:
        FileExistsError: The target exists.
        OSError: The move failed otherwise.
    """
    if sys.platform == "win32":
        with_sharing_retries(partial(staged.rename, target))
        return
    try:
        os.link(staged, target)
    except FileExistsError:
        raise
    except OSError:
        # No hard links on this file system: check, then replace.
        if _exists(target):
            raise FileExistsError(target.name) from None
        staged.replace(target)


def _exists(target: Path) -> bool:
    # A link counts as existing even when it is broken: replacing it is overwriting.
    try:
        target.lstat()
    except FileNotFoundError:
        return False
    return True


def _remove_stale_temps(target: Path) -> None:
    cutoff = time.time() - STALE_TEMP_S
    pattern = f"{glob.escape(target.name)}.*{TEMP_SUFFIX}"
    for leftover in target.parent.glob(pattern):
        with suppress(OSError):
            found = leftover.lstat()
            if stat.S_ISREG(found.st_mode) and found.st_mtime < cutoff:
                leftover.unlink()


def _remove_quietly(path: Path) -> None:
    # The original failure is the one to report; a temp file left here is removed once stale.
    with suppress(OSError):
        path.unlink(missing_ok=True)
