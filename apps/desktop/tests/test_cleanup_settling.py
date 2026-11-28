"""The settling of interrupted cleanups and the bucket identity they belong to (architecture §3.3 "Unknown batch").

A record belongs to the bucket the store was opened for: a hash of the normalised endpoint and
the bucket name, never the endpoint itself. An R2 error about R2 itself (unreachable, the
credentials, the bucket) stops the settling, and every record waits for the next refresh; one
about a single object keeps that record and the others go on.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr

from support.credentials import TEST_ACCESS_KEY_ID, TEST_BUCKET, TEST_SECRET, moto_bucket
from support.runs import RunsWorld
from support.storage import store_client
from support.upload import upload_world
from transcribe_ui.cleanup.pending import finish_pending_cleanups, record_pending
from transcribe_ui.credentials.classes import R2Credentials
from transcribe_ui.state.models import PendingCleanupRow
from transcribe_ui.storage.client import bucket_identity
from transcribe_ui.storage.constants import ERROR_R2_FAILED, ERROR_R2_UNREACHABLE
from transcribe_ui.storage.errors import StorageError
from transcribe_ui.storage.r2 import ObjectInfo, R2ObjectStore

# Two jobs of other computers whose cleanup was interrupted.
JOBS = ("batch_20260101_090000", "batch_20260101_100000")
# One endpoint written in two ways, and the bucket.
ENDPOINT = "https://ACCOUNT.r2.cloudflarestorage.com:443/"
SAME_ENDPOINT = "https://account.r2.cloudflarestorage.com"
BUCKET = "jobs"


def _credentials(endpoint: str, bucket: str = BUCKET) -> R2Credentials:
    return R2Credentials.model_validate(
        {
            "endpoint_url": endpoint,
            "bucket": bucket,
            "access_key_id": SecretStr(TEST_ACCESS_KEY_ID),
            "secret_access_key": SecretStr(TEST_SECRET),
        },
    )


def test_the_identity_hashes_the_normalised_bucket() -> None:
    """Case, the default port and a trailing slash do not change it; another bucket does; the endpoint is not in it."""
    identity = bucket_identity(_credentials(ENDPOINT))
    same = bucket_identity(_credentials(SAME_ENDPOINT))
    other = bucket_identity(_credentials(SAME_ENDPOINT, bucket="other"))
    assert (identity == same, identity == other, "account" in identity, len(identity)) == (True, False, False, 64)


class _FailingHeads(R2ObjectStore):
    """The test bucket, whose HEADs fail with one code; counts them."""

    def __init__(self, code: str) -> None:
        """Fail every HEAD with `code`."""
        super().__init__(store_client(), TEST_BUCKET, identity="test-bucket")
        self._code = code
        self.heads = 0

    def head(self, key: str) -> ObjectInfo | None:
        """Fail."""
        self.heads += 1
        raise StorageError(self._code, "head", key)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[RunsWorld]:
    """A backend over moto."""
    with moto_bucket(), upload_world(tmp_path / "leg") as created:
        yield created


def _settled(world: RunsWorld, code: str) -> tuple[tuple[str, ...], int, int]:
    """Record both jobs, settle with HEADs failing with `code`; return the finished, the HEADs, the records left."""
    store = _FailingHeads(code)
    context = world.context()
    for batch_id in JOBS:
        record_pending(context, store, batch_id)
    finished = finish_pending_cleanups(context, store)
    with world.ports.state.session() as session:
        left = session.query(PendingCleanupRow).count()
    return finished, store.heads, left


def test_r2_unreachable_stops_the_settling(world: RunsWorld) -> None:
    """R2 itself failing stops at the first record; both records wait for the next refresh."""
    assert _settled(world, ERROR_R2_UNREACHABLE) == ((), 1, len(JOBS))


def test_one_object_failing_lets_the_others_go_on(world: RunsWorld) -> None:
    """An error about one object keeps its record and goes on to the next."""
    assert _settled(world, ERROR_R2_FAILED) == ((), len(JOBS), len(JOBS))
