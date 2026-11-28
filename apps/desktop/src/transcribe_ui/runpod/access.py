"""How request handlers reach Runpod: the transport, the retry waits and the clock.

The API key comes from the credential store (`Ports.credentials`: keyring first, then the
environment), which answers `credentials_missing` or `credentials_invalid` itself.
"""

from dataclasses import dataclass, replace

import httpx
from pydantic import SecretStr

from transcribe_ui.api.errors import DomainError
from transcribe_ui.credentials.constants import CREDENTIAL_ORDER
from transcribe_ui.credentials.store import CredentialStore
from transcribe_ui.runpod.client import RunpodClient
from transcribe_ui.runpod.constants import (
    CHECK_ATTEMPTS,
    CHECK_CONNECT_TIMEOUT_S,
    CHECK_READ_TIMEOUT_S,
    CHECK_RETRY_WAIT_MAX_S,
    ERROR_CLOUD_UNREACHABLE,
    ERROR_RUNPOD_KEY_REJECTED,
    ERROR_RUNPOD_KEY_SCOPE,
    HTTP_FORBIDDEN,
    HTTP_UNAUTHORIZED,
    SECRET_FOR_ENV,
)
from transcribe_ui.runpod.errors import RunpodApiError
from transcribe_ui.runpod.timing import Clock, Stopped, Timing, Wait, never_stopped, utc_now, wait_unless_stopped


@dataclass(frozen=True)
class RunpodAccess:
    """Port of the Runpod API: transport, retry waits and clock."""

    # None: the network. Tests: an `httpx.MockTransport` over the fake API.
    transport: httpx.BaseTransport | None = None
    # Waits between attempts (tests record them instead of sleeping).
    wait: Wait = wait_unless_stopped
    # Clock that `Retry-After` dates are measured against.
    now: Clock = utc_now

    def client(self, api_key: SecretStr, stopped: Stopped = never_stopped) -> RunpodClient:
        """Return a client with this key (close it after use).

        Args:
            api_key: The desktop's Runpod API key (from the credential store).
            stopped: The request's cancel check; retry waits end early and no further
                attempt starts once it returns True.
        """
        return RunpodClient(api_key, transport=self.transport, timing=self.timing(stopped))

    def check_client(self, api_key: SecretStr, stopped: Stopped = never_stopped) -> RunpodClient:
        """Return a client with the credential check's short limits (see `check_timing`)."""
        return RunpodClient(api_key, transport=self.transport, timing=self.check_timing(stopped))

    def check_timing(self, stopped: Stopped = never_stopped) -> Timing:
        """Return the credential check's timing: 2 attempts, 5 s connect, 10 s read, waits of at most 1 s.

        So a hanging or rate-limiting API is reported within about 30 s.
        """
        return replace(
            self.timing(stopped),
            attempts=CHECK_ATTEMPTS,
            connect_timeout_s=CHECK_CONNECT_TIMEOUT_S,
            read_timeout_s=CHECK_READ_TIMEOUT_S,
            wait_max_s=CHECK_RETRY_WAIT_MAX_S,
        )

    def timing(self, stopped: Stopped = never_stopped) -> Timing:
        """Return the waits, clock and cancel check of a client."""
        return Timing(wait=self.wait, now=self.now, stopped=stopped)


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
    return DomainError(ERROR_CLOUD_UNREACHABLE, message, {"cause": error.message})


def setup_credentials(store: CredentialStore) -> tuple[SecretStr, dict[str, SecretStr]]:
    """Return the Runpod API key and the values of the R2 secrets, by Runpod secret name.

    Raises:
        DomainError: `credentials_missing` naming every missing credential (all five are
            checked first), or `credentials_invalid`.
    """
    lookup = store.lookup(CREDENTIAL_ORDER)  # one keyring read for all five
    lookup.require(CREDENTIAL_ORDER)
    api_key = lookup.runpod_api_key()
    r2 = lookup.r2()
    endpoint = SecretStr(r2.endpoint_url)
    parts = (endpoint, SecretStr(r2.bucket), r2.access_key_id, r2.secret_access_key)
    names = [secret for _, secret in SECRET_FOR_ENV]
    return api_key, dict(zip(names, parts, strict=True))
