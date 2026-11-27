"""Extra moto buckets: one per parity leg of the upload tests, or per interrupted attempt (plan m5 M5.10)."""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import boto3

from support.batches import batch_ports
from support.credentials import TEST_ACCESS_KEY_ID, TEST_ENDPOINT, TEST_SECRET
from support.runpod import Cloud, credential_store
from support.runpod_api import FAKE_API_KEY
from support.runs import JSON_TYPE
from support.storage import store_client
from transcribe_ui.api.ports import Ports
from transcribe_ui.storage.r2 import R2ObjectStore

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client

# Prefix of the moto buckets of the parity legs.
LEG_BUCKET_PREFIX = "upload-leg-"


def create_bucket(name: str) -> None:
    """Create one more bucket in the running moto mock."""
    _setup_client().create_bucket(Bucket=name)


@cache
def _setup_client() -> "S3Client":
    """Return one client for bucket creation (moto serves it whenever a mock is active)."""
    return boto3.session.Session().client(
        "s3",
        region_name="us-east-1",
        endpoint_url=TEST_ENDPOINT,
        aws_access_key_id="setup",
        aws_secret_access_key="setup",  # noqa: S106 -- moto ignores credentials.
    )


@cache
def shared_client() -> "S3Client":
    """Return one client of the app's settings for the tests' own reads (moto serves it in every mock)."""
    return store_client()


def bucket_store(bucket: str) -> R2ObjectStore:
    """Return a store on a moto bucket."""
    return R2ObjectStore(shared_client(), bucket)


@dataclass
class UploadLegs:
    """Ports factory for the parity runner: per leg its own moto bucket and fake Runpod API, seeded alike.

    Run it inside `moto_bucket()`.
    """

    # Keys written into each leg's bucket before the first request.
    seed: Sequence[str] = ()
    # The legs' buckets, in creation order (backend leg first).
    buckets: list[str] = field(default_factory=list)

    def __call__(self, root: Path) -> Ports:
        """Return the ports of one leg."""
        bucket = f"{LEG_BUCKET_PREFIX}{len(self.buckets) + 1}"
        create_bucket(bucket)
        self.buckets.append(bucket)
        store = bucket_store(bucket)
        for key in self.seed:
            store.write(key, b"{}", JSON_TYPE)
        credentials = credential_store(
            {
                "r2_endpoint_url": TEST_ENDPOINT,
                "r2_bucket": bucket,
                "r2_access_key_id": TEST_ACCESS_KEY_ID,
                "r2_secret_access_key": TEST_SECRET,
                "runpod_api_key": FAKE_API_KEY,
            },
        )
        return replace(batch_ports(root), credentials=credentials, runpod=Cloud().access())
