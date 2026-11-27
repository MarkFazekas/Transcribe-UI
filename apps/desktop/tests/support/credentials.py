"""Keyring fakes, test credentials and the moto R2 bucket (plan m5, M5.5)."""

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field

import boto3
from keyring.errors import KeyringLocked, NoKeyringError
from moto import mock_aws

from transcribe_ui.credentials.store import CredentialStore
from transcribe_ui.storage.client import quiet_sdk_loggers

# Endpoint of the moto bucket; `.invalid` never resolves, so a missing mock cannot reach a network.
TEST_ENDPOINT = "https://r2.example.invalid"
# Name of the moto bucket.
TEST_BUCKET = "transcribe-test"
# Sentinel credentials: tests assert none of them shows up in a response or a log.
TEST_ACCESS_KEY_ID = "AKIDSENTINEL0123456789"
TEST_SECRET = "SECRETSENTINEL-r2-0123456789abcdef"  # noqa: S105 -- a fake credential for moto.
TEST_RUNPOD_KEY = "rpa_RUNPODSENTINEL0123456789"
# Every sentinel credential value.
SENTINELS = (TEST_ACCESS_KEY_ID, TEST_SECRET, TEST_RUNPOD_KEY)
# Values that must never show up in a response or a log (the endpoint and bucket included).
NEVER_SHOWN = (*SENTINELS, TEST_ENDPOINT, TEST_BUCKET)


@dataclass
class MemoryKeyring:
    """A keyring backend that keeps entries in a dict."""

    entries: dict[tuple[str, str], str] = field(default_factory=dict)

    def get_password(self, service: str, username: str) -> str | None:  # noqa: WPS615 -- keyring's API.
        """Return the stored value, or None."""
        return self.entries.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:  # noqa: WPS615 -- keyring's API.
        """Store the value."""
        self.entries[service, username] = password


class NoKeyring:
    """The `keyring` fail backend: no keyring on this system (headless Linux, WSL)."""

    def get_password(self, service: str, username: str) -> str | None:  # noqa: WPS615 -- keyring's API.
        """Refuse.

        Raises:
            NoKeyringError: Always.
        """
        raise NoKeyringError(service, username)

    def set_password(self, service: str, username: str, _password: str) -> None:  # noqa: WPS615 -- keyring's API.
        """Refuse.

        Raises:
            NoKeyringError: Always.
        """
        raise NoKeyringError(service, username)


@dataclass
class LockedKeyring:
    """A keyring that fails after `allowed_writes` successful writes (a locked keychain)."""

    allowed_writes: int = 0
    inner: MemoryKeyring = field(default_factory=MemoryKeyring)

    def get_password(self, service: str, username: str) -> str | None:  # noqa: WPS615 -- keyring's API.
        """Refuse.

        Raises:
            KeyringLocked: Always.
        """
        raise KeyringLocked(service, username)

    def set_password(self, service: str, username: str, password: str) -> None:  # noqa: WPS615 -- keyring's API.
        """Store the first `allowed_writes` values, then refuse.

        Raises:
            KeyringLocked: Once the allowed writes are used up.
        """
        if self.allowed_writes <= 0:
            raise KeyringLocked(service)
        self.allowed_writes -= 1
        self.inner.set_password(service, username, password)


def memory_store(environ: Mapping[str, str] | None = None) -> CredentialStore:
    """Return a credential store on an empty in-memory keyring."""
    backend = MemoryKeyring()
    return CredentialStore(lambda: backend, environ or {})


def store_request(**overrides: str) -> str:
    """Return a `store_credentials` request document with every test credential."""
    fields = {
        "r2_endpoint_url": TEST_ENDPOINT,
        "r2_bucket": TEST_BUCKET,
        "r2_access_key_id": TEST_ACCESS_KEY_ID,
        "r2_secret_access_key": TEST_SECRET,
        "runpod_api_key": TEST_RUNPOD_KEY,
        **overrides,
    }
    return json.dumps({"action": "store_credentials", **fields})


@contextmanager
def moto_bucket() -> Iterator[None]:
    """Serve `TEST_BUCKET` at `TEST_ENDPOINT` from moto for the duration of the block.

    moto learns the endpoint from `MOTO_S3_CUSTOM_ENDPOINTS`, which `conftest.py` sets before
    moto first loads its S3 routes.
    """
    quiet_sdk_loggers()  # Before the setup client, whose debug records name the endpoint.
    with mock_aws():
        # moto refuses bucket creation in region "auto"; the app's client uses "auto".
        setup = boto3.session.Session().client(
            "s3",
            region_name="us-east-1",
            endpoint_url=TEST_ENDPOINT,
            aws_access_key_id="setup",
            aws_secret_access_key="setup",  # noqa: S106 -- moto ignores credentials.
        )
        setup.create_bucket(Bucket=TEST_BUCKET)
        yield
