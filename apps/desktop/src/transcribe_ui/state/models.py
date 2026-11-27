"""SQLAlchemy models: the one schema of the app's SQLite database (plan m5 §1 "State").

Rows keep their details as Pydantic payloads in JSON columns, next to indexed status columns
that requests filter on (§3.3), so most later changes touch payload classes, not tables.
Every timestamp comes from the injected clock and is stored in UTC.
"""

from datetime import UTC, datetime
from typing import Literal, override

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Dialect,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from transcribe_ui.state.constants import (
    BATCH_ID_LENGTH,
    CASCADE,
    CONSTRAINT_NAMING_CONVENTION,
    LANGUAGE_CODE_LENGTH,
    STATUS_LENGTH,
    ULID_COLUMN_LENGTH,
)

# Phase of a batch (§3.3); the column holds one of these.
type BatchPhase = Literal["composing", "reviewing", "uploading", "ready", "transcribed", "downloaded", "cleaned"]
# Status of a run (plan m5 §1 "Run activity"); the column holds one of these.
type RunStatus = Literal["launching", "queued", "running", "finished", "failed", "cancelled", "launch_failed"]
# A Pydantic model dumped in JSON mode, as stored in a payload column.
type Payload = dict[str, JsonValue]


class UtcDateTime(TypeDecorator[datetime]):
    """A timezone-aware datetime stored as naive UTC (SQLite keeps no time zone)."""

    impl = DateTime
    cache_ok = True

    @override
    def process_bind_param(self, moment: datetime | None, dialect: Dialect) -> datetime | None:
        """Convert an aware datetime to naive UTC; a naive one is refused.

        Raises:
            ValueError: If the datetime has no time zone.
        """
        if moment is None:
            return None
        if moment.tzinfo is None:
            msg = "Timestamps must be timezone-aware."
            raise ValueError(msg)
        return moment.astimezone(UTC).replace(tzinfo=None)

    @override
    def process_result_value(self, moment: datetime | None, dialect: Dialect) -> datetime | None:
        """Mark the stored naive UTC value as UTC."""
        return None if moment is None else moment.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    """Base of every table; constraint names are fixed, so batch migrations can find them."""

    metadata = MetaData(naming_convention=CONSTRAINT_NAMING_CONVENTION)


class BatchRow(Base):
    """A batch (§3.3): its phase and flags as columns, everything else in the payload."""

    __tablename__ = "batches"

    batch_id: Mapped[str] = mapped_column(String(BATCH_ID_LENGTH), primary_key=True)
    phase: Mapped[str] = mapped_column(String(STATUS_LENGTH), index=True)
    # Upload complete, manifest kept back while a run is active (plan m5 §1, requirements "Runpod control").
    manifest_held: Mapped[bool] = mapped_column(default=False)
    # The audio objects were cleaned up; results stay downloadable, retry is refused.
    audio_deleted: Mapped[bool] = mapped_column(default=False)
    # A leftover manifest of this batch still to be withdrawn from R2 (`PendingWithdrawal`), or None.
    pending_withdrawal: Mapped[Payload | None] = mapped_column(JSON(none_as_null=True), nullable=True, default=None)
    # Hidden from the default batch list.
    hidden: Mapped[bool] = mapped_column(default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)
    payload: Mapped[Payload] = mapped_column(JSON)


class ItemRow(Base):
    """A source file of a batch, with its own phase (an item can go back to review alone)."""

    __tablename__ = "items"

    item_id: Mapped[str] = mapped_column(String(ULID_COLUMN_LENGTH), primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.batch_id", ondelete=CASCADE), index=True)
    phase: Mapped[str] = mapped_column(String(STATUS_LENGTH), index=True)
    payload: Mapped[Payload] = mapped_column(JSON)


class JobRow(Base):
    """One target language of an item: a transcribe or an extract job; at most one per language."""

    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("item_id", "language"),)

    job_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.item_id", ondelete=CASCADE), index=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.batch_id", ondelete=CASCADE), index=True)
    language: Mapped[str] = mapped_column(String(LANGUAGE_CODE_LENGTH))
    kind: Mapped[str] = mapped_column(String(STATUS_LENGTH))
    status: Mapped[str] = mapped_column(String(STATUS_LENGTH), index=True)
    payload: Mapped[Payload] = mapped_column(JSON)


class AudioObjectRow(Base):
    """An audio object of an item in R2 (one per audio track used), with its upload status."""

    __tablename__ = "audio_objects"

    object_key: Mapped[str] = mapped_column(String, primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.batch_id", ondelete=CASCADE), index=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.item_id", ondelete=CASCADE), index=True)
    status: Mapped[str] = mapped_column(String(STATUS_LENGTH), index=True)
    payload: Mapped[Payload] = mapped_column(JSON)


class ExtractedSubtitleRow(Base):
    """The SRT of an `extract` job, kept locally until it is placed (§3.3)."""

    __tablename__ = "extracted_subtitles"

    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.job_id", ondelete=CASCADE), primary_key=True)
    srt_text: Mapped[str] = mapped_column(Text)
    payload: Mapped[Payload] = mapped_column(JSON)


class ProbeCacheRow(Base):
    """An ffprobe result, valid while the file keeps its path, size and mtime (§3.2)."""

    __tablename__ = "probe_cache"

    path: Mapped[str] = mapped_column(String, primary_key=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    mtime_ns: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    probed_at: Mapped[datetime] = mapped_column(UtcDateTime)
    payload: Mapped[Payload] = mapped_column(JSON)


class RunRow(Base):
    """A worker run (one pod or queued pod), recorded before the pod is requested (§3.7)."""

    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(ULID_COLUMN_LENGTH), primary_key=True)
    status: Mapped[str] = mapped_column(String(STATUS_LENGTH), index=True)
    pod_name: Mapped[str] = mapped_column(String, unique=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)
    payload: Mapped[Payload] = mapped_column(JSON)


class PlacementRow(Base):
    """The placement outcome of one job in one download (placed or skipped, and why)."""

    __tablename__ = "placements"

    placement_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.job_id", ondelete=CASCADE), index=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.batch_id", ondelete=CASCADE), index=True)
    outcome: Mapped[str] = mapped_column(String(STATUS_LENGTH), index=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    payload: Mapped[Payload] = mapped_column(JSON)
