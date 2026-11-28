"""Responses of the requests about jobs in the cloud: `list_cloud_batches`, `adopt_cloud_batch` (architecture §3.9)."""

from typing import Literal

from pydantic import NonNegativeFloat, NonNegativeInt

from transcribe_contracts.classes import BatchId
from transcribe_ui.api.base import FrozenModel
from transcribe_ui.batches.responses import BatchSummary, OkResponse


class CloudBatch(FrozenModel):
    """A job in the cloud: a manifest in R2 that reads."""

    batch_id: BatchId
    # Whether this database knows the job (architecture §3.9 "Known batches"); False: another computer's.
    known: bool
    files: NonNegativeInt
    jobs: NonNegativeInt
    # Jobs with a result in the cloud.
    done_jobs: NonNegativeInt
    audio_s: NonNegativeFloat
    # Where it stands: no result yet, some results, or `_done.json` (every job has a result).
    state: Literal["waiting", "transcribing", "transcribed"]


class ListCloudBatchesResponse(OkResponse):
    """Every job in the cloud, by batch id."""

    action: Literal["list_cloud_batches"] = "list_cloud_batches"
    batches: tuple[CloudBatch, ...]


class AdoptCloudBatchResponse(OkResponse):
    """Another computer's job, now in this computer's jobs."""

    action: Literal["adopt_cloud_batch"] = "adopt_cloud_batch"
    batch: BatchSummary
