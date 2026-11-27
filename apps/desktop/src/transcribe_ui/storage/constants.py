"""Constants of the R2 object store (architecture §6; the same settings as the worker's)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

# R2 region name (R2 ignores it, botocore needs one).
R2_REGION: Final = "auto"
# Attempts per S3 request, the first included (botocore "standard" retry mode, with backoff); the
# worker's `max_attempts=5` means 5 retries, so the same 6 in all.
S3_TOTAL_ATTEMPTS: Final = 6
# Seconds to wait for a connection to R2, as in the worker.
S3_CONNECT_TIMEOUT_S: Final = 10
# Seconds to wait for data on an open R2 connection, as in the worker.
S3_READ_TIMEOUT_S: Final = 30
# Attempts of the credential check, the first included: it reports quickly instead of riding out an outage.
CHECK_TOTAL_ATTEMPTS: Final = 2
# Seconds to wait for a connection during the credential check.
CHECK_CONNECT_TIMEOUT_S: Final = 5
# Seconds to wait for data during the credential check.
CHECK_READ_TIMEOUT_S: Final = 10
# Most parts an S3 (and R2) multipart upload may have.
MAX_PARTS: Final = 10_000
# Objects of at least this many bytes (64 MiB) are uploaded and copied in parts of this size.
MULTIPART_BYTES: Final = 67_108_864
# Loggers of the AWS SDK and its HTTP stack; held at WARNING, since their debug records carry
# request headers (the R2 access key id).
SDK_LOGGERS: Final = ("boto3", "botocore", "s3transfer", "urllib3")

# HTTP status of a conditional PUT whose object already exists.
HTTP_PRECONDITION_FAILED: Final = 412
# S3 error codes meaning "no such object".
S3_MISSING_CODES: Final = frozenset(("404", "NoSuchKey", "NotFound"))
# S3 error code of a multipart upload that no longer exists (already completed or aborted).
S3_NO_SUCH_UPLOAD: Final = "NoSuchUpload"
# S3 error codes meaning the access key id or the secret is wrong.
S3_AUTH_CODES: Final = frozenset(
    (
        "AuthorizationHeaderMalformed",
        "ExpiredToken",
        "InvalidAccessKeyId",
        "InvalidToken",
        "SignatureDoesNotMatch",
        "Unauthorized",
    ),
)
# S3 error codes meaning the credentials are valid but not allowed to do this.
S3_DENIED_CODES: Final = frozenset(("AccessDenied", "AllAccessDisabled"))
# S3 error code of a bucket that does not exist.
S3_NO_SUCH_BUCKET: Final = "NoSuchBucket"
# HTTP status of a request without valid credentials.
HTTP_UNAUTHORIZED: Final = 401
# HTTP status of a forbidden request.
HTTP_FORBIDDEN: Final = 403

# Error code: R2 refused the access key id or the secret.
ERROR_R2_AUTH_FAILED: Final = "r2_auth_failed"
# Error code: the credentials are valid but not allowed to do this (token scope).
ERROR_R2_ACCESS_DENIED: Final = "r2_access_denied"
# Error code: the bucket does not exist.
ERROR_R2_BUCKET_NOT_FOUND: Final = "r2_bucket_not_found"
# Error code: R2 could not be reached (DNS, connection, timeout).
ERROR_R2_UNREACHABLE: Final = "r2_unreachable"
# Error code: the source object of a copy does not exist.
ERROR_R2_OBJECT_MISSING: Final = "r2_object_missing"
# Error code: the file needs more than `MAX_PARTS` parts.
ERROR_R2_TOO_LARGE: Final = "r2_object_too_large"
# Error code: any other R2 failure.
ERROR_R2_FAILED: Final = "r2_failed"
# Error code -> message of the R2 errors: fixed texts, never a value or the SDK's message.
R2_ERROR_MESSAGES: Final[Mapping[str, str]] = MappingProxyType(
    {
        ERROR_R2_AUTH_FAILED: "R2 refused the credentials: the access key id or the secret access key is wrong.",
        ERROR_R2_ACCESS_DENIED: "R2 accepted the credentials but they are not allowed to do this; check the API token's permissions and bucket.",
        ERROR_R2_BUCKET_NOT_FOUND: "The R2 bucket does not exist; check the bucket name and the endpoint URL.",
        ERROR_R2_UNREACHABLE: "R2 could not be reached; check the network and the endpoint URL.",
        ERROR_R2_OBJECT_MISSING: "The R2 object to copy does not exist.",
        ERROR_R2_TOO_LARGE: "The file is too large for an R2 upload (at most 10 000 parts of 64 MiB).",
        ERROR_R2_FAILED: "The R2 request failed; the details are in the log.",
    },
)
