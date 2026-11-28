"""Pydantic models of the local state."""

from pydantic import AwareDatetime, BaseModel, ConfigDict

from transcribe_ui.api.base import FrozenModel, ToolPath


class ToolPaths(BaseModel):
    """The ffmpeg/ffprobe override of the settings file, read on its own.

    Other fields are ignored, so a problem elsewhere in the file never fails a tool call; a
    broken path field still does.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    ffmpeg_path: ToolPath | None = None
    ffprobe_path: ToolPath | None = None


class LiveLease(FrozenModel):
    """A run lease (`runs/<run_id>/launch.json`) that counts: the batches a live run may process."""

    run_id: str
    # The listed batches; None for a lease that cannot be read (it then lists every batch).
    batch_ids: frozenset[str] | None
    # The moment it stops counting: its deadline, capped at its R2 write time + `LEASE_MAX_SPAN_S`.
    until: AwareDatetime
    # Its R2 write time (`LastModified`; None when R2 did not give one).
    written_at: AwareDatetime | None = None

    def lists(self, batch_id: str) -> bool:
        """Return whether the run may process the batch."""
        return self.batch_ids is None or batch_id in self.batch_ids
