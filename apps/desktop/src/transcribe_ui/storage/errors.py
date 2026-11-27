"""R2 failures as domain errors with fixed messages (architecture §6).

The SDK's own messages are never passed on: they can quote URLs, bucket names and parameters.
An error carries the operation and the S3 error code or the exception type only, and is raised
`from None`, so not even a logged traceback shows the SDK's exception.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from boto3.exceptions import Boto3Error
from botocore.exceptions import BotoCoreError, ClientError, HTTPClientError
from botocore.exceptions import ConnectionError as BotoConnectionError

from transcribe_ui.api.errors import DomainError
from transcribe_ui.storage.constants import (
    ERROR_R2_ACCESS_DENIED,
    ERROR_R2_AUTH_FAILED,
    ERROR_R2_BUCKET_NOT_FOUND,
    ERROR_R2_FAILED,
    ERROR_R2_OBJECT_MISSING,
    ERROR_R2_UNREACHABLE,
    HTTP_FORBIDDEN,
    HTTP_UNAUTHORIZED,
    R2_ERROR_MESSAGES,
    S3_AUTH_CODES,
    S3_DENIED_CODES,
    S3_MISSING_CODES,
    S3_NO_SUCH_BUCKET,
)

logger = logging.getLogger(__name__)


class StorageError(DomainError):
    """An R2 request failed; the code says how (`r2_*`)."""

    def __init__(self, code: str, operation: str, cause: str) -> None:
        """Use the code's fixed message; `cause` is an S3 error code or an exception type name."""
        message = R2_ERROR_MESSAGES[code]
        super().__init__(code, message, {"operation": operation, "cause": cause})


class ObjectMissingError(StorageError):
    """The object an operation needs does not exist."""

    def __init__(self, operation: str) -> None:
        """Use the `r2_object_missing` code."""
        super().__init__(ERROR_R2_OBJECT_MISSING, operation, "NoSuchKey")


def error_code(error: ClientError) -> str:
    """Return the S3 error code of a client error ("" when absent)."""
    return str(error.response.get("Error", {}).get("Code", ""))


def http_status(error: ClientError) -> int | None:
    """Return the HTTP status of a client error."""
    return error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")


def is_missing(error: ClientError) -> bool:
    """Return whether the error means "no such object"."""
    return error_code(error) in S3_MISSING_CODES


def _client_error_code(error: ClientError) -> str:
    code = error_code(error)
    status = http_status(error)
    if code in S3_AUTH_CODES or status == HTTP_UNAUTHORIZED:
        return ERROR_R2_AUTH_FAILED
    if code in S3_DENIED_CODES or status == HTTP_FORBIDDEN:
        return ERROR_R2_ACCESS_DENIED
    if code == S3_NO_SUCH_BUCKET:
        return ERROR_R2_BUCKET_NOT_FOUND
    return ERROR_R2_FAILED


def _sdk_error_code(error: BotoCoreError | Boto3Error) -> str:
    if isinstance(error, BotoConnectionError | HTTPClientError):
        return ERROR_R2_UNREACHABLE
    return ERROR_R2_FAILED


@contextmanager
def translated(operation: str) -> Iterator[None]:
    """Turn SDK exceptions raised inside the block into `StorageError`s.

    Raises:
        StorageError: For any S3 client error or SDK failure.
    """
    try:
        yield
    except ClientError as error:
        cause = error_code(error) or str(http_status(error))
        failure = StorageError(_client_error_code(error), operation, cause)
        logger.warning("R2 %s failed: %s (%s)", operation, failure.code, cause)
        raise failure from None
    except (BotoCoreError, Boto3Error) as error:
        cause = type(error).__name__
        failure = StorageError(_sdk_error_code(error), operation, cause)
        logger.warning("R2 %s failed: %s (%s)", operation, failure.code, cause)
        raise failure from None
