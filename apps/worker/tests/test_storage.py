"""Regression test for the worker's R2 client configuration."""

from typing import Any

from transcribe_worker.pipeline.config import R2Settings
from transcribe_worker.pipeline.storage import r2_client


def test_r2_client_counts_total_attempts() -> None:
    """The retry config is total attempts (botocore `max_attempts` would count retries)."""
    settings = R2Settings.model_validate(
        {
            "endpoint_url": "https://r2.example.com",
            "bucket": "bucket",
            "access_key_id": "key",
            "secret_access_key": "secret",
        },
    )
    config: Any = r2_client(settings).meta.config  # botocore sets the options dynamically.
    assert config.retries == {"total_max_attempts": 6, "mode": "standard"}
