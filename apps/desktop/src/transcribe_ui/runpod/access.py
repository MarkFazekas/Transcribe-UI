"""How request handlers reach Runpod: the injected credentials lookup and transport.

The credentials lookup is the small interface this sub-package needs from the credential store
(M5.5 implements it; `api/wiring.py` joins the two). Until then the lookup finds nothing and
every Runpod request answers `credentials_missing`.
"""

import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx
from pydantic import SecretStr

from transcribe_ui.api.errors import DomainError
from transcribe_ui.runpod.classes import R2Credentials
from transcribe_ui.runpod.client import RunpodClient
from transcribe_ui.runpod.constants import (
    CREDENTIAL_RUNPOD_API_KEY,
    ERROR_CLOUD_UNREACHABLE,
    ERROR_CREDENTIALS_MISSING,
    ERROR_RUNPOD_KEY_REJECTED,
    ERROR_RUNPOD_KEY_SCOPE,
    HTTP_FORBIDDEN,
    HTTP_UNAUTHORIZED,
)
from transcribe_ui.runpod.errors import RunpodApiError
from transcribe_ui.runpod.session import Sleep


class CloudCredentials(Protocol):
    """Lookup of the stored credentials the Runpod requests need; None when not stored."""

    def runpod_api_key(self) -> SecretStr | None:
        """Return the desktop's Runpod API key."""
        ...

    def r2_credentials(self) -> R2Credentials | None:
        """Return the R2 credentials (endpoint, bucket, key id, secret)."""
        ...


@dataclass(frozen=True)
class StaticCredentials:
    """Credentials given up front; the default (nothing) until the credential store is wired in."""

    api_key: SecretStr | None = None
    r2: R2Credentials | None = None

    def runpod_api_key(self) -> SecretStr | None:
        """Return the Runpod API key given."""
        return self.api_key

    def r2_credentials(self) -> R2Credentials | None:
        """Return the R2 credentials given."""
        return self.r2


@dataclass(frozen=True)
class RunpodAccess:
    """Port of the Runpod API: credentials lookup, transport and the wait between retries."""

    credentials: CloudCredentials = field(default_factory=StaticCredentials)
    # None: the network. Tests: an `httpx.MockTransport` over the fake API.
    transport: httpx.BaseTransport | None = None
    sleep: Sleep = time.sleep

    def client(self) -> RunpodClient:
        """Return a client with the stored key (close it after use).

        Raises:
            DomainError: `credentials_missing` when no Runpod API key is stored.
        """
        api_key = self.credentials.runpod_api_key()
        if api_key is None:
            raise missing_credentials([CREDENTIAL_RUNPOD_API_KEY])
        return RunpodClient(api_key, transport=self.transport, sleep=self.sleep)


def missing_credentials(names: list[str]) -> DomainError:
    """Return the `credentials_missing` error naming the missing credentials (never values)."""
    message = f"Credentials missing: {', '.join(names)}. Store them first."
    return DomainError(ERROR_CREDENTIALS_MISSING, message, {"missing": list(names)})


def api_failure(error: RunpodApiError) -> DomainError:
    """Return the domain error of a failed Runpod call (its message carries no request data)."""
    if error.status == HTTP_UNAUTHORIZED:
        message = "Runpod rejected the API key (expired, revoked or mistyped). Store a valid key."
        return DomainError(ERROR_RUNPOD_KEY_REJECTED, message)
    if error.status == HTTP_FORBIDDEN:
        message = (
            f"The Runpod API key lacks the permission {error.scope!r}. Create a key with the scopes the app lists."
        )
        return DomainError(ERROR_RUNPOD_KEY_SCOPE, message, {"scope": error.scope})
    message = f"Runpod could not be reached or answered unexpectedly ({error.message}). Try again later."
    return DomainError(ERROR_CLOUD_UNREACHABLE, message)
