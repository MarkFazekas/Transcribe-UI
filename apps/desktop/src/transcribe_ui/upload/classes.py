"""The `run_upload` request, and the outcomes of the manifest step and of `publish_held_manifests` (§3.3)."""

from typing import Literal

from transcribe_contracts.classes import BatchId
from transcribe_ui.api.base import FrozenModel
from transcribe_ui.runs.classes import ActivitySource

# What the manifest step did: uploaded the manifest (batch `ready`), or kept it back during a run.
type ManifestOutcome = Literal["published", "held"]


class RunUploadRequest(FrozenModel):
    """Start or resume a batch's upload: extract, convert, upload and verify, then publish its manifest."""

    action: Literal["run_upload"]
    batch_id: BatchId


class ManifestStep(FrozenModel):
    """The outcome of one batch's manifest step."""

    batch_id: str
    outcome: ManifestOutcome
    # The signs of the active run that kept the manifest back (empty when published).
    activity: tuple[ActivitySource, ...] = ()


class HeldPublication(FrozenModel):
    """What `publish_held_manifests` did with the batches whose manifest was held back."""

    # Now `ready`, by batch id.
    published: tuple[str, ...] = ()
    # Still held, because a run is active (see `activity`).
    held: tuple[str, ...] = ()
    # Skipped this time because another request holds the batch's lock.
    busy: tuple[str, ...] = ()
    # Skipped because the upload is not complete (an item back in review); the upload publishes it.
    incomplete: tuple[str, ...] = ()
    # Batches whose pending withdrawal of a leftover manifest was finished.
    withdrawn: tuple[str, ...] = ()
    activity: tuple[ActivitySource, ...] = ()
