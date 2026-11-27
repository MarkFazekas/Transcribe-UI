"""What the upload of one item works with, and the source-unchanged check (architecture §3.3 "Upload")."""

import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from transcribe_ui.api.errors import CancelledError, DomainError
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.probe_cache import FileStat
from transcribe_ui.batches.records import StoredItem
from transcribe_ui.media.classes import ProbeResult
from transcribe_ui.media.constants import ERROR_MEDIA_OUTPUT_UNWRITABLE
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.errors import SourceUnreadableError


class SourceChangedError(Exception):
    """The item's file changed or vanished since it was probed; the item goes back to review."""


@dataclass(frozen=True)
class UploadRun:
    """One `run_upload`: its context, the object store, the batch's temp folder and the request action."""

    context: RequestContext
    store: ObjectStore
    temp_folder: Path
    action: str


def require_unchanged(member: StoredItem) -> None:
    """Check that the item's file has the size and mtime it was probed with (not for a retry item's copies).

    A file that cannot be stat-ed for another reason than being gone (an unreachable network
    share) is not taken as changed: the request fails and re-sending resumes.

    Raises:
        SourceChangedError: If it changed or is gone.
        SourceUnreadableError: If it cannot be stat-ed right now.
    """
    payload = member.payload
    if payload.copy_sources:
        return
    if _current_stat(member) != FileStat(payload.size_bytes, payload.mtime_ns):
        raise SourceChangedError


def _current_stat(member: StoredItem) -> FileStat | None:
    """Return the file's size and mtime, None when it is gone (or not a regular file any more)."""
    try:
        found = Path(member.payload.path).stat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as error:
        raise SourceUnreadableError(member.item_id) from error
    if not stat.S_ISREG(found.st_mode):
        return None
    return FileStat(found.st_size, found.st_mtime_ns)


def probe_of(member: StoredItem) -> ProbeResult:
    """Return the item's probe result (a resolved item is probed)."""
    probe = member.payload.probe
    assert probe is not None, "a resolved item is probed"
    return probe


def media_call[ResultT](member: StoredItem, call: Callable[[], ResultT]) -> ResultT:
    """Run a media tool for the item; its errors (other than a cancel) name the item in their details.

    Raises:
        DomainError: The tool's error, with `item_id` added.
    """
    try:
        return call()
    except CancelledError:
        raise
    except DomainError as error:
        error.details["item_id"] = member.item_id
        raise


def unreadable_error(member: StoredItem, path: Path) -> DomainError:
    """Return the retryable error of a local file that cannot be read while the source is unchanged.

    The source itself gives `source_unreadable`; the converted temp file `media_output_unwritable`.
    """
    if path == Path(member.payload.path):
        return SourceUnreadableError(member.item_id)
    message = "The converted audio cannot be read back from the temp folder; send the request again."
    return DomainError(ERROR_MEDIA_OUTPUT_UNWRITABLE, message, {"operation": "upload", "item_id": member.item_id})
