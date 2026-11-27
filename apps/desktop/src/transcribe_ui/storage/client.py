"""The boto3 client for R2, built with the worker's settings (architecture §6)."""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config

from transcribe_ui.storage.constants import (
    R2_REGION,
    S3_CONNECT_TIMEOUT_S,
    S3_MAX_ATTEMPTS,
    S3_READ_TIMEOUT_S,
    SDK_LOGGERS,
)
from transcribe_ui.storage.r2 import R2ObjectStore

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client

    from transcribe_ui.credentials.classes import R2Credentials
    from transcribe_ui.credentials.store import CredentialStore

# Builds the S3 client for R2 from the credentials (tests: the same settings, served by moto).
type R2ClientFactory = Callable[[R2Credentials], S3Client]


def r2_client(credentials: "R2Credentials") -> "S3Client":
    """Build an S3 client for R2 (checksums only when required: R2 rejects some defaults).

    A session per client: boto3's default session is not safe to share between threads.
    """
    for name in SDK_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    config = Config(
        region_name=R2_REGION,
        retries={"max_attempts": S3_MAX_ATTEMPTS, "mode": "standard"},
        connect_timeout=S3_CONNECT_TIMEOUT_S,
        read_timeout=S3_READ_TIMEOUT_S,
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    )
    session = boto3.session.Session()
    return session.client(
        "s3",
        endpoint_url=credentials.endpoint_url,
        aws_access_key_id=credentials.access_key_id.get_secret_value(),
        aws_secret_access_key=credentials.secret_access_key.get_secret_value(),
        config=config,
    )


def open_object_store(credentials: "CredentialStore", clients: R2ClientFactory) -> R2ObjectStore:
    """Return the object store on the configured bucket.

    Raises:
        DomainError: `credentials_missing` or `credentials_invalid`.
    """
    r2 = credentials.r2()
    return R2ObjectStore(clients(r2), r2.bucket)
