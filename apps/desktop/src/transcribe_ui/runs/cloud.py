"""The cloud as the run-activity check, the manifest step and the launch scope read it: R2 and the Runpod pods."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.credentials.constants import CREDENTIAL_ORDER
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
    ports = context.ports
    lookup = ports.credentials.lookup(CREDENTIAL_ORDER)
    lookup.require(CREDENTIAL_ORDER)
    r2 = lookup.r2()
    api_key = lookup.runpod_api_key()
    store = R2ObjectStore(ports.r2_clients(r2, TRANSFER_LIMITS), r2.bucket)
    with ports.runpod.client(api_key, context.cancel.is_cancelled) as client:
        yield CloudAccess(store, client.pods)
