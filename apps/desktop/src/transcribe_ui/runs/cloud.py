"""The cloud as the requests open it: R2 alone, or R2 and the Runpod API (architecture §3.9 "Roles").

Every installation has the R2 credentials; the Runpod key is optional. Uploads, cleanups, the
run-scope (lease) checks and the results poll use R2 alone; only the launch, the stops and the
cloud machines need the key.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.credentials.constants import CREDENTIAL_ORDER, RUNPOD_API_KEY
from transcribe_ui.runpod.client import RunpodClient
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.storage.client import TRANSFER_LIMITS, bucket_identity, open_object_store
from transcribe_ui.storage.r2 import ObjectStore, R2ObjectStore


@dataclass(frozen=True)
class CloudAccess:
    """The object store and the Runpod pods API of one request."""

    store: ObjectStore
    pods: PodsApi


def open_store(context: RequestContext) -> ObjectStore:
    """Return R2 opened with the stored R2 credentials (no call yet, so a credentials error changes nothing).

    Raises:
        DomainError: `credentials_missing` or `credentials_invalid` for the R2 credentials, or a
            `StorageError` when the R2 client cannot be built.
    """
    ports = context.ports
    return open_object_store(ports.credentials, ports.r2_clients)


def runpod_key_set(context: RequestContext) -> bool:
    """Return whether a Runpod API key is stored (or set in the environment); `Lookup.runpod_key_set` has the rule."""
    return context.ports.credentials.lookup((RUNPOD_API_KEY,)).runpod_key_set()


@contextmanager
def open_cloud_client(context: RequestContext) -> Iterator[tuple[CloudAccess, RunpodClient]]:
    """Open R2 and the whole Runpod client (one keyring read for all five credentials; no call yet).

    The Runpod client follows the request's cancel token and is closed when the block ends.

    Raises:
        DomainError: `credentials_missing` or `credentials_invalid` (names, never values), or a
            `StorageError` when the R2 client cannot be built.
    """
    ports = context.ports
    lookup = ports.credentials.lookup(CREDENTIAL_ORDER)
    lookup.require(CREDENTIAL_ORDER)
    r2 = lookup.r2()
    api_key = lookup.runpod_api_key()
    client_of_r2 = ports.r2_clients(r2, TRANSFER_LIMITS)
    store = R2ObjectStore(client_of_r2, r2.bucket, identity=bucket_identity(r2))
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
            self._store = open_store(self._context)
        return self._store
