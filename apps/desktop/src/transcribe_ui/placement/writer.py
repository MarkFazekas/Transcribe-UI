"""Writing files into place: a temp file next to the target, then `os.replace` (architecture §3.5).

An interrupted write never leaves a partial target: the bytes go to `<target>.tmp` in the target's
folder (the planner's name and path limits keep room for the suffix), are flushed to disk, and
`os.replace` moves the file onto the target, replacing an existing one on every OS (Windows
included). A failed write removes its temp file; a temp file a killed process left behind is
overwritten by the next write of the same target.
"""

import os
from pathlib import Path

from transcribe_ui.placement.classes import PlacementOutcome
from transcribe_ui.placement.constants import TEMP_SUFFIX


def temp_path(target: Path) -> Path:
    """Return the temp file a target is first written to."""
    return target.with_name(target.name + TEMP_SUFFIX)


def write_atomically(target: Path, body: bytes) -> None:
    """Write the bytes to the target through its temp file.

    Raises:
        OSError: The temp file could not be written or moved (it is removed).
    """
    staged = temp_path(target)
    try:
        _write_synced(staged, body)
        staged.replace(target)
    except BaseException:
        _remove_quietly(staged)
        raise


def place_file(target: Path, body: bytes, *, overwrite: bool, next_to_media: bool) -> PlacementOutcome:
    """Write a subtitle to its target as the plan says, checking the target again first.

    Next to the media the folder must still exist (it is never created there: a missing original
    folder is skipped); the other strategies create their folders. A target that exists by
    now is skipped unless overwriting. Returns what was done.

    Raises:
        OSError: A folder could not be created or the file could not be written.
    """
    folder = target.parent
    if next_to_media:
        if not folder.is_dir():
            return "skip_folder_missing"
    else:
        folder.mkdir(parents=True, exist_ok=True)
    exists = _exists(target)
    if exists and not overwrite:
        return "skip_existing"
    write_atomically(target, body)
    return "overwrite" if exists else "place"


def _exists(target: Path) -> bool:
    # A link counts as existing even when it is broken: replacing it is overwriting.
    try:
        target.lstat()
    except FileNotFoundError:
        return False
    return True


def _write_synced(path: Path, body: bytes) -> None:
    with path.open("wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # The original failure is the one to report; a temp file left here is overwritten by the
        # next write of the same target.
        return
