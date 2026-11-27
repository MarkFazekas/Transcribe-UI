"""Failure paths of the R2 object store: error mapping, aborts, no leaks (plan m5, M5.5).

S3 errors come from botocore's `Stubber`, connection failures from a patched client method,
so no test reaches a network.
"""

import traceback
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

import pytest
from botocore.exceptions import ConnectTimeoutError, EndpointConnectionError, ProfileNotFound
from botocore.stub import Stubber

from support.credentials import TEST_ACCESS_KEY_ID, TEST_BUCKET, TEST_ENDPOINT, moto_bucket
from support.storage import (
    AUDIO,
    BATCH_PREFIX,
    BIG_KEY,
    LARGE_SIZE,
    PART_BYTES,
    FailingCall,
    ignore_progress,
    make_file,
    r2_credentials,
    store_client,
)
from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.storage.client import r2_client
from transcribe_ui.storage.constants import MAX_PARTS
from transcribe_ui.storage.errors import StorageError
from transcribe_ui.storage.r2 import R2ObjectStore

# The call of `upload_part` that fails.
FAILING_PART = 2
JSON = "application/json"
KEPT_KEY = "batch_1/b.json"
MISSING_BUCKET = "no-such-bucket"
# Every test checks the log records for credentials.
pytestmark = pytest.mark.usefixtures("no_credential_logs")


class ProgressFailedError(Exception):
    """Raised by a progress callback."""


def _raised(action: Callable[[], object]) -> StorageError:
    """Run the action and return the `StorageError` it must raise."""
    try:
        action()
    except StorageError as error:
        return error
    pytest.fail("no StorageError was raised")


def _failing_progress(_sent: int) -> None:
    raise ProgressFailedError


def test_unknown_bucket_is_reported() -> None:
    """Listing a bucket that does not exist gives `r2_bucket_not_found`."""
    with moto_bucket():
        store = R2ObjectStore(r2_client(r2_credentials(MISSING_BUCKET)), MISSING_BUCKET)
        failure = _raised(store.check_access)
    assert failure.code == "r2_bucket_not_found"
    assert failure.details == {"operation": "list", "cause": "NoSuchBucket"}


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
    client = store_client()
    store = R2ObjectStore(client, TEST_BUCKET)
    with Stubber(client) as stubber:
        stubber.add_client_error(
            "list_objects_v2",
            service_error_code=s3_code,
            service_message=f"message naming {TEST_ACCESS_KEY_ID}",
            http_status_code=http_status,
        )
        failure = _raised(store.check_access)
    assert failure.code == expected
    assert TEST_ACCESS_KEY_ID not in str(failure)
    assert TEST_ACCESS_KEY_ID not in failure.body().model_dump_json()


@pytest.mark.parametrize(
    "error",
    [EndpointConnectionError(endpoint_url=TEST_ENDPOINT), ConnectTimeoutError(endpoint_url=TEST_ENDPOINT)],
)
def test_connection_failures_are_unreachable(error: Exception, monkeypatch: pytest.MonkeyPatch) -> None:
    """Connection errors and timeouts become `r2_unreachable` without the endpoint in the message."""
    client = store_client()
    monkeypatch.setattr(client, "list_objects_v2", Mock(side_effect=error))
    failure = _raised(R2ObjectStore(client, TEST_BUCKET).check_access)
    assert failure.code == "r2_unreachable"
    assert TEST_ENDPOINT not in failure.body().model_dump_json()


def test_failed_part_aborts_the_upload(
    r2_store: R2ObjectStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A part that fails aborts the multipart upload and raises a `StorageError`."""
    client = store_client()
    monkeypatch.setattr(client, "upload_part", FailingCall(client.upload_part, FAILING_PART))
    failing = R2ObjectStore(client, TEST_BUCKET, part_bytes=PART_BYTES)
    failure = _raised(
        lambda: failing.upload_file(BIG_KEY, make_file(tmp_path, LARGE_SIZE), AUDIO, ignore_progress, CancelFlag())
    )
    assert failure.code == "r2_unreachable"
    assert not r2_store.abort_uploads(BATCH_PREFIX)
    assert r2_store.head(BIG_KEY) is None


def test_copy_in_a_missing_bucket_names_the_bucket() -> None:
    """A copy whose HEAD gets 404 tells a missing bucket from a missing object."""
    with moto_bucket():
        store = R2ObjectStore(r2_client(r2_credentials(MISSING_BUCKET)), MISSING_BUCKET)
        failure = _raised(lambda: store.copy("src/a.mka", "retry/a.mka"))
    assert failure.code == "r2_bucket_not_found"


@pytest.mark.parametrize(
    ("operation", "s3_code", "http_status", "expected"),
    [
        ("head_object", "AccessDenied", 403, "r2_access_denied"),
        ("get_object", "InternalError", 500, "r2_failed"),
        ("get_object", "NoSuchBucket", 404, "r2_bucket_not_found"),
        ("put_object", "SignatureDoesNotMatch", 403, "r2_auth_failed"),
    ],
)
def test_object_errors_map_to_codes(operation: str, s3_code: str, http_status: int, expected: str) -> None:
    """head, read and write turn S3 errors into `r2_*` codes, never the server's text."""
    client = store_client()
    store = R2ObjectStore(client, TEST_BUCKET)
    calls: dict[str, Callable[[], object]] = {
        "head_object": lambda: store.head(KEPT_KEY),
        "get_object": lambda: store.read(KEPT_KEY),
        "put_object": lambda: store.write(KEPT_KEY, b"{}", JSON),
    }
    with Stubber(client) as stubber:
        stubber.add_client_error(
            operation,
            service_error_code=s3_code,
            service_message=f"message naming {TEST_ENDPOINT}",
            http_status_code=http_status,
        )
        failure = _raised(calls[operation])
    assert failure.code == expected
    assert TEST_ENDPOINT not in "".join(traceback.format_exception(failure))


def test_sdk_messages_stay_out_of_tracebacks(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SDK's exception (which names the endpoint and bucket) is not chained to the error."""
    client = store_client()
    sdk_error = EndpointConnectionError(endpoint_url=f"{TEST_ENDPOINT}/{TEST_BUCKET}")
    monkeypatch.setattr(client, "list_objects_v2", Mock(side_effect=sdk_error))
    failure = _raised(R2ObjectStore(client, TEST_BUCKET).check_access)
    rendered = "".join(traceback.format_exception(failure))
    assert TEST_ENDPOINT not in rendered
    assert TEST_BUCKET not in rendered


def test_broken_aws_profile_is_a_storage_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An SDK failure while building the client (a missing AWS profile) is an `r2_*` error."""
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "missing-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "missing-credentials"))
    monkeypatch.setenv("AWS_PROFILE", "no-such-profile")
    failure = _raised(store_client)
    assert failure.code == "r2_failed"
    assert failure.details == {"operation": "connect", "cause": ProfileNotFound.__name__}


def test_too_many_parts_is_refused_before_upload(r2_store: R2ObjectStore, tmp_path: Path) -> None:
    """A file needing more than 10 000 parts is refused before a multipart upload starts."""
    tiny_parts = R2ObjectStore(store_client(), TEST_BUCKET, part_bytes=1)
    source = make_file(tmp_path, MAX_PARTS + 1)
    failure = _raised(lambda: tiny_parts.upload_file(BIG_KEY, source, AUDIO, ignore_progress, CancelFlag()))
    assert failure.code == "r2_object_too_large"
    assert not r2_store.abort_uploads(BATCH_PREFIX)


def test_failed_complete_aborts_the_upload(
    r2_store: R2ObjectStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing `CompleteMultipartUpload` aborts the upload and leaves no object."""
    client = store_client()
    monkeypatch.setattr(client, "complete_multipart_upload", FailingCall(client.complete_multipart_upload, 1))
    failing = R2ObjectStore(client, TEST_BUCKET, part_bytes=PART_BYTES)
    _raised(lambda: failing.upload_file(BIG_KEY, make_file(tmp_path, LARGE_SIZE), AUDIO, ignore_progress, CancelFlag()))
    assert not r2_store.abort_uploads(BATCH_PREFIX)
    assert r2_store.head(BIG_KEY) is None


def test_failing_progress_aborts_the_upload(r2_store: R2ObjectStore, tmp_path: Path) -> None:
    """An exception from the progress callback propagates and aborts the multipart upload."""
    with pytest.raises(ProgressFailedError):
        r2_store.upload_file(BIG_KEY, make_file(tmp_path, LARGE_SIZE), AUDIO, _failing_progress, CancelFlag())
    assert not r2_store.abort_uploads(BATCH_PREFIX)
    assert r2_store.head(BIG_KEY) is None
