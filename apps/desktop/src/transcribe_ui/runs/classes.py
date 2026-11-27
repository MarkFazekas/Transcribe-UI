"""Models of the run-activity check and the launch scope (architecture §3.7)."""

from typing import Literal

from pydantic import NonNegativeFloat, NonNegativeInt

from transcribe_contracts.classes import TranscriptionMode
from transcribe_ui.api.base import FrozenModel

# Where a sign of a live worker run was found: a local run row, the Runpod pod or queued-pod
# list, or a worker heartbeat (`runs/<run_id>/status.json`) in R2.
type ActivityKind = Literal["local_run", "pod", "queued_pod", "heartbeat"]
# Why a manifest under `batch_metadata/` is not part of the scope: a key that is not a manifest
# key, another schema major version, or an object that does not validate or match its key.
type UnreadableReason = Literal["not_a_manifest", "unsupported_schema", "invalid"]


class ActivitySource(FrozenModel):
    """One sign of a live worker run."""

    kind: ActivityKind
    # Run id (local run), pod or queued-pod name, or the heartbeat's object key.
    name: str
    # The run's, pod's or queued pod's status, or the worker state (`unreadable` if it cannot be read).
    status: str
    # The run it belongs to, so a frontend can name it: the local run, the run in the pod name
    # (None for a name that does not parse) or the run of the heartbeat.
    run_id: str | None = None


class RunActivity(FrozenModel):
    """Every sign of a live worker run found by one check; none means no run is active."""

    sources: tuple[ActivitySource, ...]

    @property
    def active(self) -> bool:
        """Return whether a run is active."""
        return bool(self.sources)


class PendingJob(FrozenModel):
    """A job of a manifest that has no result JSON yet."""

    item_id: str
    language: str
    mode: TranscriptionMode
    # Duration of the job's audio object.
    duration_s: NonNegativeFloat


class ScopeBatch(FrozenModel):
    """A batch the next worker run processes: its manifest has no `_done.json` yet."""

    batch_id: str
    # Whether this database knows the batch (a batch of another computer is unknown).
    known: bool
    # Whether its manifest is still held back locally (published by `launch_worker` before the run).
    held: bool
    # Files (manifest items) of the batch.
    files: NonNegativeInt
    pending_jobs: tuple[PendingJob, ...]
    pending_duration_s: NonNegativeFloat


class UnreadableManifest(FrozenModel):
    """An object under `batch_metadata/` the worker skips too (architecture §4)."""

    key: str
    reason: UnreadableReason


class LaunchScope(FrozenModel):
    """What the next worker run would process: every manifest without `_done.json`, plus held ones."""

    # By batch id.
    batches: tuple[ScopeBatch, ...]
    # By key.
    unreadable: tuple[UnreadableManifest, ...]
    pending_jobs: NonNegativeInt
    pending_duration_s: NonNegativeFloat
    # Hash of the covered batch ids, the pending job keys and the total duration (plan m5 §1
    # "Launch scope fingerprint"): `launch_worker` launches only on the scope it confirmed.
    fingerprint: str
