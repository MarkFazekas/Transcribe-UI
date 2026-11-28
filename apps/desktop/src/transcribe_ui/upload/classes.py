"""The `run_upload` request (§3.3)."""

from typing import Literal

from transcribe_contracts.classes import BatchId
from transcribe_ui.api.base import FrozenModel


class RunUploadRequest(FrozenModel):
    """Start or resume a batch's upload: extract, convert, upload and verify, then publish its manifest."""

    action: Literal["run_upload"]
    batch_id: BatchId
