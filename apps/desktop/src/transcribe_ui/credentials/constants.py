"""Constants of the credential store (architecture §3.6)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

# Keyring service name under which every credential of the app is stored.
KEYRING_SERVICE: Final = "transcribe-ui"

# Credential name of the R2 endpoint URL (`https://<account>.r2.cloudflarestorage.com`).
R2_ENDPOINT_URL: Final = "r2_endpoint_url"
# Credential name of the R2 bucket.
R2_BUCKET: Final = "r2_bucket"
# Credential name of the R2 access key id.
R2_ACCESS_KEY_ID: Final = "r2_access_key_id"
# Credential name of the R2 secret access key.
R2_SECRET_ACCESS_KEY: Final = "r2_secret_access_key"  # noqa: S105 -- a credential name, not a secret.
# Credential name of the desktop's Runpod API key.
RUNPOD_API_KEY: Final = "runpod_api_key"

# Every credential, in the order responses list them (and `StoreCredentialsRequest` declares them).
CREDENTIAL_ORDER: Final = (R2_ENDPOINT_URL, R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, RUNPOD_API_KEY)
# The credentials an R2 connection needs.
R2_CREDENTIALS: Final = (R2_ENDPOINT_URL, R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY)
# Credential name -> environment variable of the fallback (the names of `.env` and of the worker).
ENVIRONMENT_VARIABLES: Final[Mapping[str, str]] = MappingProxyType(
    {
        R2_ENDPOINT_URL: "R2_ENDPOINT_URL",
        R2_BUCKET: "R2_BUCKET",
        R2_ACCESS_KEY_ID: "R2_ACCESS_KEY_ID",
        R2_SECRET_ACCESS_KEY: "R2_SECRET_ACCESS_KEY",
        RUNPOD_API_KEY: "RUNPOD_API_KEY",
    },
)

# Longest accepted key, secret or token (characters); real ones are far shorter. Fits the
# Windows Credential Manager's 2560-byte limit even as UTF-16.
TOKEN_MAX_LENGTH: Final = 1024
# Longest accepted endpoint URL (characters); the same keyring limit applies.
ENDPOINT_MAX_LENGTH: Final = 1024
# Required scheme of the R2 endpoint URL.
ENDPOINT_SCHEME: Final = "https"
# Paths an endpoint URL may have (none: the bucket is a separate credential).
ENDPOINT_PATHS: Final = frozenset(("", "/"))
# R2 bucket name rule: 3-63 lower-case letters, digits and hyphens, not starting or ending with a hyphen.
BUCKET_PATTERN: Final = r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$"

# Validator messages: fixed texts that never contain the value.
# A key, secret or token is empty after dropping surrounding white space.
VALUE_EMPTY: Final = "must not be empty"
# A key, secret or token is longer than `TOKEN_MAX_LENGTH`.
VALUE_TOO_LONG: Final = f"must be at most {TOKEN_MAX_LENGTH} characters"
# A key, secret, token or URL is not ASCII, or contains white space or control characters.
VALUE_NOT_COMPACT: Final = "must be ASCII without spaces or control characters"
# A key, secret or token is only `*` characters: a masked value pasted back, not the real one.
VALUE_MASKED: Final = "is a masked value (only '*' characters); enter the real one"
# Character a masked `SecretStr` is shown with.
MASK_CHARACTER: Final = "*"
# The endpoint URL is not an https URL with only a host (and port).
ENDPOINT_RULE: Final = "must be an https URL with a host name only (https://<account>.r2.cloudflarestorage.com)"
# The bucket name breaks `BUCKET_PATTERN`.
BUCKET_RULE: Final = "must be 3-63 lower-case letters, digits and hyphens, not starting or ending with a hyphen"
# A `store_credentials` request sets no credential.
NOTHING_TO_STORE: Final = "set at least one credential"

# Error code: a credential an operation needs is set neither in the keyring nor in the environment.
ERROR_CREDENTIALS_MISSING: Final = "credentials_missing"
# Error code: a stored or environment credential does not have a valid form.
ERROR_CREDENTIALS_INVALID: Final = "credentials_invalid"
# Error code: this system has no keyring backend; the environment variables are the way.
ERROR_KEYRING_UNAVAILABLE: Final = "keyring_unavailable"
# Error code: the keyring backend failed (locked, refused, broken).
ERROR_KEYRING_FAILED: Final = "keyring_failed"

# Message of a passed R2 check.
R2_CHECK_PASSED: Final = "R2 accepted the credentials and the bucket can be listed."
