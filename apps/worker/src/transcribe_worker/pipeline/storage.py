"""Object storage used by the pipeline: a protocol and the R2 (S3 API) implementation."""

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import ClientError

from transcribe_worker.pipeline.constants import (
    HTTP_PRECONDITION_FAILED,
    R2_REGION,
    S3_MAX_ATTEMPTS,
    S3_MISSING_CODES,
    S3_MULTIPART_BYTES,
)

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client

    from transcribe_worker.pipeline.config import R2Settings


class ObjectStore(Protocol):
    """The few object operations the pipeline needs; every method retries transient errors."""

    def list_keys(self, prefix: str) -> list[str]:
        """Return every key under the prefix."""

    def read(self, key: str) -> bytes | None:
        """Return the object's body, or None if it does not exist."""

    def download(self, key: str, target: Path) -> None:
        """Download the object to a local file."""

    def write(self, key: str, body: bytes, content_type: str) -> None:
        """Create or replace the object."""

    def write_new(self, key: str, body: bytes, content_type: str) -> bool:
        """Create the object only if it does not exist; return False if it already existed."""


class S3ObjectStore:
    """ObjectStore on an S3-compatible API (Cloudflare R2 in production, moto in tests)."""

    def __init__(self, client: "S3Client", bucket: str) -> None:
        """Wrap a boto3 S3 client for one bucket."""
        self._client = client
        self._bucket = bucket
        self._transfer = TransferConfig(
            multipart_threshold=S3_MULTIPART_BYTES,
            multipart_chunksize=S3_MULTIPART_BYTES,
        )

    def list_keys(self, prefix: str) -> list[str]:
        """Return every key under the prefix (all pages)."""
        paginator = self._client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=self._bucket, Prefix=prefix)
        return [entry["Key"] for page in pages for entry in page.get("Contents", [])]

    def read(self, key: str) -> bytes | None:
        """Return the object's body, or None if it does not exist."""
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if _error_code(error) in S3_MISSING_CODES:
                return None
            raise
        body: bytes = response["Body"].read()
        return body

    def download(self, key: str, target: Path) -> None:
        """Download the object to a local file (multipart for large objects)."""
        self._client.download_file(
            Bucket=self._bucket,
            Key=key,
            Filename=f"{target}",
            Config=self._transfer,
        )

    def write(self, key: str, body: bytes, content_type: str) -> None:
        """Create or replace the object."""
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )

    def write_new(self, key: str, body: bytes, content_type: str) -> bool:
        """Create the object with `If-None-Match: *`; return False if it already existed."""
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
                IfNoneMatch="*",
            )
        except ClientError as error:
            if _http_status(error) == HTTP_PRECONDITION_FAILED:
                return False
            raise
        return True


def r2_client(settings: "R2Settings") -> "S3Client":
    """Build an S3 client for R2 (checksums only when required: R2 rejects some defaults)."""
    config = Config(
        region_name=R2_REGION,
        retries={"max_attempts": S3_MAX_ATTEMPTS, "mode": "standard"},
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    )
    return boto3.client(
        "s3",
        endpoint_url=str(settings.endpoint_url),
        aws_access_key_id=settings.access_key_id.get_secret_value(),
        aws_secret_access_key=settings.secret_access_key.get_secret_value(),
        config=config,
    )


def _error_code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))


def _http_status(error: ClientError) -> int | None:
    return error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
