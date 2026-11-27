"""The R2 object store against moto, and its error mapping (plan m5, M5.5).

moto serves the test endpoint with the app's own client settings (`r2_client`); the error
mapping uses botocore's `Stubber` or a patched client method, so no test reaches a network.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import Mock

import pytest
from botocore.exceptions import ConnectTimeoutError, EndpointConnectionError
from botocore.stub import Stubber
from pydantic import SecretStr

from support.credentials import TEST_ACCESS_KEY_ID, TEST_BUCKET, TEST_ENDPOINT, TEST_SECRET, moto_bucket
from transcribe_ui.api.errors import CancelledError, DomainError
from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.credentials.classes import R2Credentials
from transcribe_ui.storage.client import r2_client
from transcribe_ui.storage.errors import ObjectMissingError, StorageError
from transcribe_ui.storage.r2 import ObjectInfo, R2ObjectStore

# Smallest part S3 (and moto) accepts; the tests' multipart size.
PART_BYTES = 5 * 1024 * 1024
# Bytes of the short last part of the large file.
LAST_PART_BYTES = 1234
# A file of two full parts and a short third one.
LARGE_SIZE = 2 * PART_BYTES + LAST_PART_BYTES
# Size of a file below the part size.
SMALL_SIZE = 1000
# Byte values of the generated files cycle below this prime, so parts differ.
BYTE_CYCLE = 251
# Multipart uploads started under the aborted prefix.
ABORTED_UPLOADS = 2
# The call of `upload_part` that fails.
FAILING_PART = 2
AUDIO = "audio/x-matroska"
JSON = "application/json"
KEPT_KEY = "batch_1/b.json"
BIG_KEY = "b/audio/big.mka"
SMALL_KEY = "b/audio/x.mka"


def _credentials(bucket: str = TEST_BUCKET) -> R2Credentials:
    return R2Credentials(
        endpoint_url=TEST_ENDPOINT,
        bucket=bucket,
        access_key_id=SecretStr(TEST_ACCESS_KEY_ID),
        secret_access_key=SecretStr(TEST_SECRET),
    )


@pytest.fixture
def store() -> Iterator[R2ObjectStore]:
    """Return the object store on an empty moto bucket, with 5 MiB parts."""
    with moto_bucket():
        yield R2ObjectStore(r2_client(_credentials()), TEST_BUCKET, part_bytes=PART_BYTES)


def _make_file(folder: Path, size: int) -> Path:
    path = folder / f"audio-{size}.mka"
    path.write_bytes(bytes(index % BYTE_CYCLE for index in range(size)))
    return path


def _ignore_progress(_sent: int) -> None:
    """Drop a progress report."""


@dataclass
class CancelAfterFirstPart:
    """Progress callback that records reports and sets the cancel flag on the first one."""

    flag: CancelFlag
    reported: list[int] = field(default_factory=list)

    def __call__(self, sent: int) -> None:
        """Record the report and request cancellation."""
        self.reported.append(sent)
        self.flag.cancel()


@dataclass
class FailingCall:
    """Wraps a client method; the `fail_on`-th call raises a connection error instead."""

    wrapped: Callable[..., object]
    fail_on: int
    calls: int = 0

    def __call__(self, **kwargs: object) -> object:
        """Pass the call on, or fail on the chosen one."""
        self.calls += 1
        if self.calls == self.fail_on:
            raise EndpointConnectionError(endpoint_url=TEST_ENDPOINT)
        return self.wrapped(**kwargs)


def test_small_objects_round_trip(store: R2ObjectStore) -> None:
    """write, read, head and list; missing objects are None, never errors."""
    store.write("batch_1/a.json", b"{}", JSON)
    store.write(KEPT_KEY, b"[1]", JSON)
    store.write("other/c.json", b"{}", JSON)
    assert store.read(KEPT_KEY) == b"[1]"
    assert store.read("batch_1/missing.json") is None
    assert store.head(KEPT_KEY) == ObjectInfo(KEPT_KEY, 3)
    assert store.head("batch_1/missing.json") is None
    assert store.list_objects("batch_1/") == (ObjectInfo("batch_1/a.json", 2), ObjectInfo(KEPT_KEY, 3))


def test_delete_treats_missing_as_success(store: R2ObjectStore) -> None:
    """Deleting twice succeeds; only the deleted object is gone."""
    store.write("batch_1/a.json", b"{}", JSON)
    store.write(KEPT_KEY, b"[1]", JSON)
    store.delete("batch_1/a.json")
    store.delete("batch_1/a.json")
    assert [listed.key for listed in store.list_objects("")] == [KEPT_KEY]
    store.check_access()


def test_write_new_does_not_replace(store: R2ObjectStore) -> None:
    """`write_new` creates once; a second call keeps the first body and returns False."""
    assert store.write_new("batch_metadata/b.json", b"first", JSON)
    assert not store.write_new("batch_metadata/b.json", b"second", JSON)
    assert store.read("batch_metadata/b.json") == b"first"


def test_small_upload_is_one_put(store: R2ObjectStore, tmp_path: Path) -> None:
    """A file smaller than a part is uploaded whole, with one progress report."""
    source = _make_file(tmp_path, SMALL_SIZE)
    reported: list[int] = []
    sent = store.upload_file(SMALL_KEY, source, AUDIO, reported.append, CancelFlag())
    assert sent == SMALL_SIZE
    assert reported == [SMALL_SIZE]
    assert store.read(SMALL_KEY) == source.read_bytes()


def test_multipart_upload_reports_each_part(store: R2ObjectStore, tmp_path: Path) -> None:
    """A large file goes up in parts, with progress after each, and reads back identical."""
    source = _make_file(tmp_path, LARGE_SIZE)
    reported: list[int] = []
    sent = store.upload_file(BIG_KEY, source, AUDIO, reported.append, CancelFlag())
    assert sent == LARGE_SIZE
    assert reported == [PART_BYTES, 2 * PART_BYTES, LARGE_SIZE]
    assert store.read(BIG_KEY) == source.read_bytes()
    assert not store.abort_uploads("b/")


def test_cancel_between_parts_aborts_the_upload(store: R2ObjectStore, tmp_path: Path) -> None:
    """Cancelling after the first part stops before the next, aborts, and leaves no object."""
    source = _make_file(tmp_path, LARGE_SIZE)
    progress = CancelAfterFirstPart(CancelFlag())
    with pytest.raises(CancelledError):
        store.upload_file(BIG_KEY, source, AUDIO, progress, progress.flag)
    assert progress.reported == [PART_BYTES]
    assert store.head(BIG_KEY) is None
    assert not store.abort_uploads("b/")


def test_cancel_before_a_small_upload(store: R2ObjectStore, tmp_path: Path) -> None:
    """A set cancel token stops a single PUT before it is sent."""
    flag = CancelFlag()
    flag.cancel()
    with pytest.raises(CancelledError):
        store.upload_file(SMALL_KEY, _make_file(tmp_path, SMALL_SIZE), AUDIO, _ignore_progress, flag)
    assert store.head(SMALL_KEY) is None


def test_abort_uploads_under_a_prefix(store: R2ObjectStore) -> None:
    """Unfinished multipart uploads under the prefix are aborted; others stay."""
    client = r2_client(_credentials())
    for key in ("b1/audio/x.mka", "b1/audio/y.mka", "b2/audio/z.mka"):
        client.create_multipart_upload(Bucket=TEST_BUCKET, Key=key)
    assert store.abort_uploads("b1/") == ABORTED_UPLOADS
    assert not store.abort_uploads("b1/")
    remaining = client.list_multipart_uploads(Bucket=TEST_BUCKET).get("Uploads", [])
    assert [upload["Key"] for upload in remaining] == ["b2/audio/z.mka"]


def test_copy_small_and_multipart(store: R2ObjectStore, tmp_path: Path) -> None:
    """Server-side copies, in parts from the part size on."""
    store.write("src/small.mka", b"abc", AUDIO)
    source = _make_file(tmp_path, LARGE_SIZE)
    store.upload_file("src/big.mka", source, AUDIO, _ignore_progress, CancelFlag())
    store.copy("src/small.mka", "retry/small.mka")
    store.copy("src/big.mka", "retry/big.mka")
    assert store.read("retry/small.mka") == b"abc"
    assert store.read("retry/big.mka") == source.read_bytes()


def test_copy_of_a_missing_object_fails(store: R2ObjectStore) -> None:
    """Copying an object that does not exist raises `r2_object_missing`."""
    with pytest.raises(ObjectMissingError) as caught:
        store.copy("src/missing.mka", "retry/missing.mka")
    assert caught.value.code == "r2_object_missing"


def test_unknown_bucket_is_reported() -> None:
    """Listing a bucket that does not exist gives `r2_bucket_not_found`."""
    with moto_bucket():
        store = R2ObjectStore(r2_client(_credentials("no-such-bucket")), "no-such-bucket")
        with pytest.raises(StorageError) as caught:
            store.check_access()
    assert caught.value.code == "r2_bucket_not_found"
    assert caught.value.details == {"operation": "list", "cause": "NoSuchBucket"}


@pytest.mark.parametrize(
    ("s3_code", "http_status", "expected"),
    [
        ("InvalidAccessKeyId", 403, "r2_auth_failed"),
        ("SignatureDoesNotMatch", 403, "r2_auth_failed"),
        ("Unauthorized", 401, "r2_auth_failed"),
        ("AccessDenied", 403, "r2_access_denied"),
        ("NoSuchBucket", 404, "r2_bucket_not_found"),
        ("InternalError", 500, "r2_failed"),
    ],
)
def test_client_errors_map_to_codes(s3_code: str, http_status: int, expected: str) -> None:
    """S3 error responses become `r2_*` codes with a fixed message (never the server's text)."""
    client = r2_client(_credentials())
    store = R2ObjectStore(client, TEST_BUCKET)
    with Stubber(client) as stubber:
        stubber.add_client_error(
            "list_objects_v2",
            service_error_code=s3_code,
            service_message=f"message naming {TEST_ACCESS_KEY_ID}",
            http_status_code=http_status,
        )
        with pytest.raises(StorageError) as caught:
            store.check_access()
    assert caught.value.code == expected
    assert TEST_ACCESS_KEY_ID not in str(caught.value)
    assert TEST_ACCESS_KEY_ID not in caught.value.body().model_dump_json()


@pytest.mark.parametrize(
    "error",
    [EndpointConnectionError(endpoint_url=TEST_ENDPOINT), ConnectTimeoutError(endpoint_url=TEST_ENDPOINT)],
)
def test_connection_failures_are_unreachable(error: Exception, monkeypatch: pytest.MonkeyPatch) -> None:
    """Connection errors and timeouts become `r2_unreachable` without the endpoint in the message."""
    client = r2_client(_credentials())
    monkeypatch.setattr(client, "list_objects_v2", Mock(side_effect=error))
    with pytest.raises(DomainError) as caught:
        R2ObjectStore(client, TEST_BUCKET).check_access()
    assert caught.value.code == "r2_unreachable"
    assert TEST_ENDPOINT not in caught.value.body().model_dump_json()


def test_failed_part_aborts_the_upload(store: R2ObjectStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A part that fails aborts the multipart upload and raises a `StorageError`."""
    client = r2_client(_credentials())
    monkeypatch.setattr(client, "upload_part", FailingCall(client.upload_part, FAILING_PART))
    failing = R2ObjectStore(client, TEST_BUCKET, part_bytes=PART_BYTES)
    with pytest.raises(StorageError) as caught:
        failing.upload_file(BIG_KEY, _make_file(tmp_path, LARGE_SIZE), AUDIO, _ignore_progress, CancelFlag())
    assert caught.value.code == "r2_unreachable"
    assert not store.abort_uploads("b/")
    assert store.head(BIG_KEY) is None
