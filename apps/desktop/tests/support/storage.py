"""Shared pieces of the R2 store tests: moto-backed store, test files, failing calls."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from botocore.exceptions import EndpointConnectionError
from pydantic import SecretStr

from support.credentials import TEST_ACCESS_KEY_ID, TEST_BUCKET, TEST_ENDPOINT, TEST_SECRET
from transcribe_ui.credentials.classes import R2Credentials
from transcribe_ui.storage.client import r2_client

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client

# Smallest part S3 (and moto) accepts; the tests' multipart size.
PART_BYTES = 5 * 1024 * 1024
# Bytes of the short last part of the large file.
LAST_PART_BYTES = 1234
# A file of two full parts and a short third one.
LARGE_SIZE = 2 * PART_BYTES + LAST_PART_BYTES
# Byte values of the generated files cycle below this prime, so parts differ.
BYTE_CYCLE = 251
# Content type of the test audio objects.
AUDIO = "audio/x-matroska"
# Prefix of the test batch's objects.
BATCH_PREFIX = "b/"
# Key of the large test object.
BIG_KEY = f"{BATCH_PREFIX}audio/big.mka"


def r2_credentials(bucket: str = TEST_BUCKET) -> R2Credentials:
    """Return the test credentials for a bucket."""
    return R2Credentials(
        endpoint_url=TEST_ENDPOINT,
        bucket=bucket,
        access_key_id=SecretStr(TEST_ACCESS_KEY_ID),
        secret_access_key=SecretStr(TEST_SECRET),
    )


def store_client() -> "S3Client":
    """Return a fresh client of the app's settings on the test bucket (serve it with `moto_bucket`)."""
    return r2_client(r2_credentials())


def make_file(folder: Path, size: int) -> Path:
    """Write a test file of `size` bytes; return its path."""
    path = folder / f"audio-{size}.mka"
    path.write_bytes(bytes(index % BYTE_CYCLE for index in range(size)))
    return path


def ignore_progress(_sent: int) -> None:
    """Drop a progress report."""


@dataclass
class FailingCall:
    """Wraps a client method; the `fail_on`-th call raises a connection error instead."""

    wrapped: Callable[..., object]
    fail_on: int
    calls: int = 0

    def __call__(self, **kwargs: object) -> object:
        """Pass the call on, or fail on the chosen one."""
        self.calls += 1
        if self.calls == self.fail_on:
            raise EndpointConnectionError(endpoint_url=TEST_ENDPOINT)
        return self.wrapped(**kwargs)
