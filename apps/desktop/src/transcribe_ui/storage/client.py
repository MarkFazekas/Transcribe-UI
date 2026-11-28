"""The boto3 client for R2, built with the worker's settings (architecture §6)."""

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import boto3
from botocore.config import Config

from transcribe_ui.storage.constants import (
    CHECK_CONNECT_TIMEOUT_S,
    CHECK_READ_TIMEOUT_S,
    CHECK_TOTAL_ATTEMPTS,
    DEFAULT_PORTS,
    R2_REGION,
    S3_CONNECT_TIMEOUT_S,
    S3_READ_TIMEOUT_S,
    S3_TOTAL_ATTEMPTS,
    SDK_LOGGERS,
)
from transcribe_ui.storage.errors import translated
from transcribe_ui.storage.r2 import R2ObjectStore

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client

    from transcribe_ui.credentials.classes import R2Credentials
    from transcribe_ui.credentials.store import CredentialStore


@dataclass(frozen=True)
class ClientLimits:
    """Attempts and timeouts of one S3 client."""

    # Attempts per request, the first included.
    attempts: int
    connect_timeout_s: float
    read_timeout_s: float


# Limits of the clients that move data: the worker's.
TRANSFER_LIMITS = ClientLimits(S3_TOTAL_ATTEMPTS, S3_CONNECT_TIMEOUT_S, S3_READ_TIMEOUT_S)
# Limits of `check_credentials`: an answer within about 30 s (2 x (5 + 10) s) even when R2 hangs.
CHECK_LIMITS = ClientLimits(CHECK_TOTAL_ATTEMPTS, CHECK_CONNECT_TIMEOUT_S, CHECK_READ_TIMEOUT_S)

# Builds the S3 client for R2 from the credentials and limits (tests: the same settings, served by moto).
type R2ClientFactory = Callable[[R2Credentials, ClientLimits], S3Client]


def quiet_sdk_loggers() -> None:
    """Hold the SDK's loggers at WARNING: their debug records carry request headers (the access key id)."""
    for name in SDK_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def r2_client(credentials: "R2Credentials", limits: ClientLimits = TRANSFER_LIMITS) -> "S3Client":
    """Build an S3 client for R2 (checksums only when required: R2 rejects some defaults).

    A session per client: boto3's default session is not safe to share between threads.

    Raises:
        StorageError: The SDK refused to build the client (a broken AWS profile, for example).
    """
    quiet_sdk_loggers()
    config = Config(
        region_name=R2_REGION,
        retries={"total_max_attempts": limits.attempts, "mode": "standard"},
        connect_timeout=limits.connect_timeout_s,
        read_timeout=limits.read_timeout_s,
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    )
    with translated("connect"):
        session = boto3.session.Session()
        return session.client(
            "s3",
            endpoint_url=credentials.endpoint_url,
            aws_access_key_id=credentials.access_key_id.get_secret_value(),
            aws_secret_access_key=credentials.secret_access_key.get_secret_value(),
            config=config,
        )


def open_object_store(
    credentials: "CredentialStore",
    clients: R2ClientFactory,
    limits: ClientLimits = TRANSFER_LIMITS,
) -> R2ObjectStore:
    """Return the object store on the configured bucket.

    Raises:
        DomainError: `credentials_missing` or `credentials_invalid`; `StorageError` when the
            client cannot be built.
    """
    r2 = credentials.r2()
    return R2ObjectStore(clients(r2, limits), r2.bucket, identity=bucket_identity(r2))


def bucket_identity(r2: "R2Credentials") -> str:
    """Return a stable, non-secret name of the bucket: a SHA-256 of the normalised endpoint and the bucket name.

    The endpoint carries the account id and is treated as a credential, so only its hash is
    kept. Normalised: scheme and host lowercased, the default port of the scheme dropped, the
    trailing slash stripped.
    """
    parts = urlsplit(str(r2.endpoint_url))
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    default = DEFAULT_PORTS.get(scheme)
    netloc = host
    if port not in {None, default}:
        netloc = f"{host}:{port}"
    path = parts.path.rstrip("/")
    named = f"{scheme}://{netloc}{path}/{r2.bucket}"
    return hashlib.sha256(named.encode()).hexdigest()
