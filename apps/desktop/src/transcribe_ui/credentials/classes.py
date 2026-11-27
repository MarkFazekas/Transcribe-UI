"""Request and response models of the credential requests, and the R2 credentials (architecture §3.6).

No validator here may put a value into its error message, and every model hides the input in
`str(ValidationError)`: a refused credential must never reach a response, a log or an exception.
"""

import re
from typing import Annotated, Literal, Self
from urllib.parse import SplitResult, urlsplit

from pydantic import AfterValidator, BeforeValidator, ConfigDict, Field, JsonValue, SecretStr, model_validator

from transcribe_ui.api.base import FrozenModel
from transcribe_ui.credentials.constants import (
    BUCKET_PATTERN,
    BUCKET_RULE,
    ENDPOINT_MAX_LENGTH,
    ENDPOINT_PATHS,
    ENDPOINT_RULE,
    ENDPOINT_SCHEME,
    MASK_CHARACTER,
    NOTHING_TO_STORE,
    TOKEN_MAX_LENGTH,
    VALUE_EMPTY,
    VALUE_MASKED,
    VALUE_NOT_COMPACT,
    VALUE_TOO_LONG,
)

# A credential the app stores (keyring entry name; `ENVIRONMENT_VARIABLES` gives its fallback).
type CredentialName = Literal[
    "r2_endpoint_url", "r2_bucket", "r2_access_key_id", "r2_secret_access_key", "runpod_api_key"
]
# Where a credential was found: the keyring, the environment fallback, or nowhere.
type CredentialSource = Literal["keyring", "environment", "missing"]
# State of the keyring: usable, no backend on this system, or the backend failed (locked, broken).
type KeyringState = Literal["available", "unavailable", "failed"]
# A service whose credentials `check_credentials` tries.
type CheckedService = Literal["r2", "runpod"]
# Result of one service check: accepted, credentials missing, or refused / unreachable.
type CheckOutcome = Literal["ok", "missing", "failed"]


def _stripped(pasted: object) -> object:
    """Drop surrounding white space of a pasted text (other types are left to the type check)."""
    if isinstance(pasted, str):
        return pasted.strip()
    return pasted


def _is_compact(text: str) -> bool:
    """Return whether the text is ASCII without white space and control characters.

    ASCII also because the values go into HTTP headers and URLs: a non-ASCII character would
    fail deep in the HTTP client (and could reach a traceback) instead of being refused here.
    """
    printable = text.isascii() and text.isprintable()
    return printable and not any(map(str.isspace, text))


def _token_problem(text: str) -> str | None:
    """Return why the text is not a valid token (a fixed message), or None."""
    if not text:
        return VALUE_EMPTY
    if len(text) > TOKEN_MAX_LENGTH:
        return VALUE_TOO_LONG
    if not _is_compact(text):
        return VALUE_NOT_COMPACT
    if set(text) == {MASK_CHARACTER}:
        return VALUE_MASKED
    return None


def _check_token(secret: SecretStr) -> SecretStr:
    problem = _token_problem(secret.get_secret_value())
    if problem is not None:
        raise ValueError(problem)
    return secret


def _has_host_only(parts: SplitResult) -> bool:
    no_user = parts.username is None and parts.password is None
    no_extras = not parts.query and not parts.fragment
    return bool(parts.hostname) and no_user and no_extras and parts.path in ENDPOINT_PATHS


def _is_endpoint(url: str) -> bool:
    if len(url) > ENDPOINT_MAX_LENGTH or not _is_compact(url):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:  # The message of this error can quote the URL, so it is dropped.
        return False
    return parts.scheme == ENDPOINT_SCHEME and port != 0 and _has_host_only(parts)


def _check_endpoint(url: str) -> str:
    if not _is_endpoint(url):
        raise ValueError(ENDPOINT_RULE)
    return url.rstrip("/")


def _check_bucket(bucket: str) -> str:
    if re.fullmatch(BUCKET_PATTERN, bucket) is None:
        raise ValueError(BUCKET_RULE)
    return bucket


# An access key, secret or API token: kept as `SecretStr`, surrounding white space dropped.
type Token = Annotated[SecretStr, BeforeValidator(_stripped), AfterValidator(_check_token)]
# The R2 endpoint URL (S3 API), without a trailing slash.
type EndpointUrl = Annotated[str, BeforeValidator(_stripped), AfterValidator(_check_endpoint)]
# An R2 bucket name.
type BucketName = Annotated[str, BeforeValidator(_stripped), AfterValidator(_check_bucket)]


class SecretModel(FrozenModel):
    """Base of models holding credentials: a validation error never shows the input."""

    model_config = ConfigDict(hide_input_in_errors=True)


class R2Credentials(SecretModel):
    """Everything an R2 connection needs."""

    endpoint_url: EndpointUrl
    bucket: BucketName
    access_key_id: Token
    secret_access_key: Token


# Requests


class StoreCredentialsRequest(SecretModel):
    """Store credentials in the keyring; fields left out keep what is stored. Values are never echoed."""

    action: Literal["store_credentials"]
    r2_endpoint_url: EndpointUrl | None = Field(default=None, description="R2 S3 API endpoint URL.")
    r2_bucket: BucketName | None = Field(default=None, description="R2 bucket name.")
    r2_access_key_id: Token | None = Field(default=None, description="R2 API token access key id.")
    r2_secret_access_key: Token | None = Field(default=None, description="R2 API token secret access key.")
    runpod_api_key: Token | None = Field(default=None, description="Runpod API key of the desktop app.")

    def to_store(self) -> dict[CredentialName, str]:
        """Return the credentials the request sets, by name, in `CREDENTIAL_ORDER`."""
        given: dict[CredentialName, SecretStr | str | None] = {
            "r2_endpoint_url": self.r2_endpoint_url,
            "r2_bucket": self.r2_bucket,
            "r2_access_key_id": self.r2_access_key_id,
            "r2_secret_access_key": self.r2_secret_access_key,
            "runpod_api_key": self.runpod_api_key,
        }
        to_store: dict[CredentialName, str] = {}
        for name, credential in given.items():
            if credential is not None:
                to_store[name] = _plain(credential)
        return to_store

    @model_validator(mode="after")
    def _check_any(self) -> Self:
        if not self.to_store():
            raise ValueError(NOTHING_TO_STORE)
        return self


def _plain(credential: SecretStr | str) -> str:
    if isinstance(credential, SecretStr):
        return credential.get_secret_value()
    return credential


class CredentialsStatusRequest(FrozenModel):
    """Report which credentials are set and where from; never their values."""

    action: Literal["credentials_status"]


class CheckCredentialsRequest(FrozenModel):
    """Try the credentials against the services (R2: list the bucket; Runpod: read the pod list); changes nothing."""

    action: Literal["check_credentials"]


# Responses


class StoreCredentialsResponse(FrozenModel):
    """The credentials were stored in the keyring."""

    status: Literal["ok"] = "ok"
    action: Literal["store_credentials"] = "store_credentials"
    stored: tuple[CredentialName, ...]


class CredentialStatus(FrozenModel):
    """Whether one credential is set, and where from."""

    name: CredentialName
    # Environment variable used when the keyring has no entry.
    environment_variable: str
    source: CredentialSource


class CredentialsStatusResponse(FrozenModel):
    """Which credentials are set and where from (keyring first, then the environment)."""

    status: Literal["ok"] = "ok"
    action: Literal["credentials_status"] = "credentials_status"
    keyring: KeyringState
    credentials: tuple[CredentialStatus, ...]


class ServiceCheck(FrozenModel):
    """Outcome of trying one service's credentials."""

    service: CheckedService
    outcome: CheckOutcome
    # The service's "check passed" code, or the error code when the outcome is not "ok" (the
    # codes of the error responses).
    code: str
    message: str
    # The error's details when the outcome is not "ok".
    details: dict[str, JsonValue] = Field(default_factory=dict)


class CheckCredentialsResponse(FrozenModel):
    """Outcome per service; a failed check is reported here, not as an error response."""

    status: Literal["ok"] = "ok"
    action: Literal["check_credentials"] = "check_credentials"
    checks: tuple[ServiceCheck, ...]
