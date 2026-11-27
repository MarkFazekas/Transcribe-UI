"""The cloud as the run-activity check, the manifest step and the launch scope read it: R2 and the Runpod pods."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.credentials.constants import CREDENTIAL_ORDER
from transcribe_ui.runpod.client import RunpodClient
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.storage.client import TRANSFER_LIMITS
from transcribe_ui.storage.r2 import ObjectStore, R2ObjectStore


@dataclass(frozen=True)
class CloudAccess:
    """The object store and the Runpod pods API of one request."""

    store: ObjectStore
    pods: PodsApi


@contextmanager
def open_cloud(context: RequestContext) -> Iterator[CloudAccess]:
    """Open R2 and the Runpod API with the stored credentials (one keyring read for all five).

    Nothing is sent yet, so a credentials error changes nothing. The Runpod client follows the
    request's cancel token and is closed when the block ends.

    Raises:
        DomainError: `credentials_missing` or `credentials_invalid` (names, never values), or a
            `StorageError` when the R2 client cannot be built.
    """
    with open_cloud_client(context) as (cloud, _client):
        yield cloud


@contextmanager
def open_cloud_client(context: RequestContext) -> Iterator[tuple[CloudAccess, RunpodClient]]:
    """Like `open_cloud`, also yielding the whole Runpod client (templates, volumes, catalog, create)."""
    ports = context.ports
    lookup = ports.credentials.lookup(CREDENTIAL_ORDER)
    lookup.require(CREDENTIAL_ORDER)
    r2 = lookup.r2()
    api_key = lookup.runpod_api_key()
    store = R2ObjectStore(ports.r2_clients(r2, TRANSFER_LIMITS), r2.bucket)
    with ports.runpod.client(api_key, context.cancel.is_cancelled) as client:
        yield CloudAccess(store, client.pods), client


@contextmanager
def open_runpod(context: RequestContext) -> Iterator[RunpodClient]:
    """Open the Runpod API alone with the stored API key (no call yet), following the request's cancel token.

    Raises:
        DomainError: `credentials_missing` or `credentials_invalid` for the Runpod key.
    """
    api_key = context.ports.credentials.runpod_api_key()
    with context.ports.runpod.client(api_key, context.cancel.is_cancelled) as client:
        yield client


class LazyStore:
    """R2 opened with the stored credentials on first use, so work that needs only Runpod runs without it.

    `refresh_status` stops a run at its deadline from Runpod data alone: missing or rejected R2
    credentials, or an R2 outage, must not keep a billing pod alive.
    """

    def __init__(self, context: RequestContext) -> None:
        """Open nothing yet."""
        self._context = context
        self._store: ObjectStore | None = None

    def get(self) -> ObjectStore:
        """Return the object store, opening it on the first call.

        Raises:
            DomainError: `credentials_missing` or `credentials_invalid` for the R2 credentials,
                or a `StorageError` when the R2 client cannot be built.
        """
        if self._store is None:
            ports = self._context.ports
            r2 = ports.credentials.r2()
            self._store = R2ObjectStore(ports.r2_clients(r2, TRANSFER_LIMITS), r2.bucket)
        return self._store
