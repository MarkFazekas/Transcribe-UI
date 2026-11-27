"""The probe cache of the review step: ffprobe results keyed by path + size + mtime (§3.2, §3.3)."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.orm import Session

from transcribe_ui.media.classes import ProbeResult
from transcribe_ui.state.models import ProbeCacheRow


@dataclass(frozen=True)
class FileStat:
    """Size and mtime of a source file."""

    size_bytes: int
    mtime_ns: int


def file_stat(path: str) -> FileStat | None:
    """Return the file's size and mtime, or None when it is gone or cannot be read."""
    source = Path(path)
    try:
        found = source.stat() if source.is_file() else None
    except OSError:
        return None
    return None if found is None else FileStat(found.st_size, found.st_mtime_ns)


def cached_probe(session: Session, path: str, stat: FileStat) -> ProbeResult | None:
    """Return the cached probe of the file in this state, or None (also for an unreadable row)."""
    cached = session.get(ProbeCacheRow, (path, stat.size_bytes, stat.mtime_ns))
    if cached is None:
        return None
    try:
        return ProbeResult.model_validate(cached.payload)
    except ValidationError:
        # Written in another payload shape: a miss, and `remember_probe` overwrites the row.
        return None


def remember_probe(session: Session, path: str, stat: FileStat, probe: ProbeResult, now: datetime) -> None:
    """Store the probe as the path's only cache entry.

    The older entries describe a file that is not there any more; an entry with this size and
    mtime is unreadable (or another batch's review just wrote the same result), so it is replaced.
    """
    session.execute(delete(ProbeCacheRow).where(ProbeCacheRow.path == path))
    session.add(
        ProbeCacheRow(
            path=path,
            size_bytes=stat.size_bytes,
            mtime_ns=stat.mtime_ns,
            probed_at=now,
            payload=probe.model_dump(mode="json"),
        ),
    )
