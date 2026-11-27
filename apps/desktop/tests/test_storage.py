"""The R2 object store against moto (plan m5, M5.5).

moto serves the test endpoint with the app's own client settings (`r2_client`), so no test
reaches a network. Failure paths are in `test_storage_errors.py`.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from support.credentials import TEST_BUCKET
from support.storage import (
    AUDIO,
    BATCH_PREFIX,
    BIG_KEY,
    LARGE_SIZE,
    PART_BYTES,
    ignore_progress,
    make_file,
    store_client,
)
from transcribe_ui.api.errors import CancelledError
from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.storage.errors import ObjectMissingError
from transcribe_ui.storage.r2 import ObjectInfo, R2ObjectStore

# Size of a file below the part size.
SMALL_SIZE = 1000
# Multipart uploads started under the aborted prefix.
ABORTED_UPLOADS = 2
JSON = "application/json"
KEPT_KEY = "batch_1/b.json"
SMALL_KEY = f"{BATCH_PREFIX}audio/x.mka"
# Every test checks the log records for credentials.
pytestmark = pytest.mark.usefixtures("no_credential_logs")


@dataclass
class CancelAfterFirstPart:
    """Progress callback that records reports and sets the cancel flag on the first one."""

    flag: CancelFlag
    reported: list[int] = field(default_factory=list)

    def __call__(self, sent: int) -> None:
        """Record the report and request cancellation."""
        self.reported.append(sent)
        self.flag.cancel()


def test_small_objects_round_trip(r2_store: R2ObjectStore) -> None:
    """write, read, head and list; missing objects are None, never errors."""
    r2_store.write("batch_1/a.json", b"{}", JSON)
    r2_store.write(KEPT_KEY, b"[1]", JSON)
    r2_store.write("other/c.json", b"{}", JSON)
    assert r2_store.read(KEPT_KEY) == b"[1]"
    assert r2_store.read("batch_1/missing.json") is None
    assert r2_store.head(KEPT_KEY) == ObjectInfo(KEPT_KEY, 3)
    assert r2_store.head("batch_1/missing.json") is None
    assert r2_store.list_objects("batch_1/") == (ObjectInfo("batch_1/a.json", 2), ObjectInfo(KEPT_KEY, 3))


def test_delete_treats_missing_as_success(r2_store: R2ObjectStore) -> None:
    """Deleting twice succeeds; only the deleted object is gone."""
    r2_store.write("batch_1/a.json", b"{}", JSON)
    r2_store.write(KEPT_KEY, b"[1]", JSON)
    r2_store.delete("batch_1/a.json")
    r2_store.delete("batch_1/a.json")
    assert [listed.key for listed in r2_store.list_objects("")] == [KEPT_KEY]
    r2_store.check_access()


def test_write_new_does_not_replace(r2_store: R2ObjectStore) -> None:
    """`write_new` creates once; a second call keeps the first body and returns False."""
    assert r2_store.write_new("batch_metadata/b.json", b"first", JSON)
    assert not r2_store.write_new("batch_metadata/b.json", b"second", JSON)
    assert r2_store.read("batch_metadata/b.json") == b"first"


def test_small_upload_is_one_put(r2_store: R2ObjectStore, tmp_path: Path) -> None:
    """A file smaller than a part is uploaded whole, with one progress report."""
    source = make_file(tmp_path, SMALL_SIZE)
    reported: list[int] = []
    sent = r2_store.upload_file(SMALL_KEY, source, AUDIO, reported.append, CancelFlag())
    assert sent == SMALL_SIZE
    assert reported == [SMALL_SIZE]
    assert r2_store.read(SMALL_KEY) == source.read_bytes()


def test_multipart_upload_reports_each_part(r2_store: R2ObjectStore, tmp_path: Path) -> None:
    """A large file goes up in parts, with progress after each, and reads back identical."""
    source = make_file(tmp_path, LARGE_SIZE)
    reported: list[int] = []
    sent = r2_store.upload_file(BIG_KEY, source, AUDIO, reported.append, CancelFlag())
    assert sent == LARGE_SIZE
    assert reported == [PART_BYTES, 2 * PART_BYTES, LARGE_SIZE]
    assert r2_store.read(BIG_KEY) == source.read_bytes()
    assert not r2_store.abort_uploads(BATCH_PREFIX)


def test_cancel_between_parts_aborts_the_upload(r2_store: R2ObjectStore, tmp_path: Path) -> None:
    """Cancelling after the first part stops before the next, aborts, and leaves no object."""
    source = make_file(tmp_path, LARGE_SIZE)
    progress = CancelAfterFirstPart(CancelFlag())
    with pytest.raises(CancelledError):
        r2_store.upload_file(BIG_KEY, source, AUDIO, progress, progress.flag)
    assert progress.reported == [PART_BYTES]
    assert r2_store.head(BIG_KEY) is None
    assert not r2_store.abort_uploads(BATCH_PREFIX)


def test_cancel_before_a_small_upload(r2_store: R2ObjectStore, tmp_path: Path) -> None:
    """A set cancel token stops a single PUT before it is sent."""
    flag = CancelFlag()
    flag.cancel()
    with pytest.raises(CancelledError):
        r2_store.upload_file(SMALL_KEY, make_file(tmp_path, SMALL_SIZE), AUDIO, ignore_progress, flag)
    assert r2_store.head(SMALL_KEY) is None


def test_abort_uploads_under_a_prefix(r2_store: R2ObjectStore) -> None:
    """Unfinished multipart uploads under the prefix are aborted; others stay."""
    client = store_client()
    for key in ("b1/audio/x.mka", "b1/audio/y.mka", "b2/audio/z.mka"):
        client.create_multipart_upload(Bucket=TEST_BUCKET, Key=key)
    assert r2_store.abort_uploads("b1/") == ABORTED_UPLOADS
    assert not r2_store.abort_uploads("b1/")
    remaining = client.list_multipart_uploads(Bucket=TEST_BUCKET).get("Uploads", [])
    assert [upload["Key"] for upload in remaining] == ["b2/audio/z.mka"]


def test_copy_small_and_multipart(r2_store: R2ObjectStore, tmp_path: Path) -> None:
    """Server-side copies, in parts from the part size on."""
    r2_store.write("src/small.mka", b"abc", AUDIO)
    source = make_file(tmp_path, LARGE_SIZE)
    r2_store.upload_file("src/big.mka", source, AUDIO, ignore_progress, CancelFlag())
    r2_store.copy("src/small.mka", "retry/small.mka")
    r2_store.copy("src/big.mka", "retry/big.mka")
    assert r2_store.read("retry/small.mka") == b"abc"
    assert r2_store.read("retry/big.mka") == source.read_bytes()


def test_copy_of_a_missing_object_fails(r2_store: R2ObjectStore) -> None:
    """Copying an object that does not exist raises `r2_object_missing`."""
    with pytest.raises(ObjectMissingError) as caught:
        r2_store.copy("src/missing.mka", "retry/missing.mka")
    assert caught.value.code == "r2_object_missing"
