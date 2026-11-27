"""What the upload of one item works with, and the source-unchanged check (architecture §3.3 "Upload")."""

from dataclasses import dataclass
from pathlib import Path

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.probe_cache import FileStat, file_stat
from transcribe_ui.batches.records import StoredItem
from transcribe_ui.media.classes import ProbeResult
from transcribe_ui.storage.r2 import ObjectStore


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

    Raises:
        SourceChangedError: If it changed or is gone.
    """
    payload = member.payload
    if payload.copy_sources:
        return
    if file_stat(payload.path) != FileStat(payload.size_bytes, payload.mtime_ns):
        raise SourceChangedError


def probe_of(member: StoredItem) -> ProbeResult:
    """Return the item's probe result (a resolved item is probed)."""
    probe = member.payload.probe
    assert probe is not None, "a resolved item is probed"
    return probe
