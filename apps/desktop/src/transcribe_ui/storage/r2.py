"""The desktop's R2 object store: an `ObjectStore` protocol and its boto3 adapter (plan m5 §1).

Not shared with the worker (apps share only contracts); `storage/client.py` builds the boto3
client with the worker's settings (architecture §6). Every method turns SDK failures into
`StorageError`s.
"""

from dataclasses import dataclass, field
from datetime import datetime
from operator import attrgetter
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError

from transcribe_ui.storage.constants import HTTP_PRECONDITION_FAILED, MULTIPART_BYTES
from transcribe_ui.storage.errors import ObjectMissingError, http_status, is_missing, translated
from transcribe_ui.storage.multipart import (
    UploadControl,
    UploadProgress,
    UploadTarget,
    abort_all,
    open_uploads,
    put_in_parts,
    put_whole,
)

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client
    from types_boto3_s3.type_defs import CopySourceTypeDef, ObjectTypeDef

    from transcribe_ui.api.ports import CancelToken


@dataclass(frozen=True)
class ObjectInfo:
    """An object's key and size, and when it was last written (R2's clock; not compared)."""

    key: str
    size: int
    modified: datetime | None = field(default=None, compare=False)


class ObjectStore(Protocol):  # noqa: WPS214 -- one method per object operation of the app.
    """The object operations of the desktop app on one bucket; missing objects are not errors."""

    def check_access(self) -> None:
        """List one key of the bucket, to prove the credentials and the bucket work."""
        ...

    def head(self, key: str) -> ObjectInfo | None:
        """Return the object's size, or None if it does not exist (or the bucket does not: HEAD cannot tell)."""
        ...

    def list_objects(self, prefix: str) -> tuple[ObjectInfo, ...]:
        """Return every object under the prefix, in key order."""
        ...

    def read(self, key: str) -> bytes | None:
        """Return the object's body, or None if it does not exist."""
        ...

    def write(self, key: str, body: bytes, content_type: str) -> None:
        """Create or replace the object."""
        ...

    def write_new(self, key: str, body: bytes, content_type: str) -> bool:
        """Create the object only if it does not exist; return False if it already existed.

        False is ambiguous after a retried request whose first attempt was written: callers
        that must know compare the stored content.
        """
        ...

    def copy(self, source_key: str, target_key: str) -> None:
        """Copy an object inside the bucket (server side; in parts when large)."""
        ...

    def delete(self, key: str) -> None:
        """Delete the object; a missing object is success."""
        ...

    def abort_uploads(self, prefix: str) -> int:
        """Abort every unfinished multipart upload under the prefix; return how many."""
        ...

    def count_uploads(self, prefix: str) -> int:
        """Return how many unfinished multipart uploads exist under the prefix."""
        ...

    def upload_file(
        self,
        key: str,
        source: Path,
        content_type: str,
        progress: UploadProgress,
        cancel: "CancelToken",
    ) -> int:
        """Upload a local file with progress, checking `cancel` between parts; return the bytes sent.

        A failure after a lost response may leave the object written: callers verify with `head`.
        """
        ...


class R2ObjectStore:  # noqa: WPS214 -- one method per `ObjectStore` operation.
    """`ObjectStore` on an S3-compatible API (Cloudflare R2 in production, moto in tests)."""

    def __init__(self, client: "S3Client", bucket: str, part_bytes: int = MULTIPART_BYTES) -> None:
        """Wrap a boto3 S3 client for one bucket; `part_bytes` is lowered only by tests."""
        self._client = client
        self._bucket = bucket
        self._part_bytes = part_bytes
        self._transfer = TransferConfig(multipart_threshold=part_bytes, multipart_chunksize=part_bytes)

    def check_access(self) -> None:
        """List one key of the bucket.

        Raises:
            StorageError: The credentials, the endpoint or the bucket do not work.
        """
        with translated("list"):
            self._client.list_objects_v2(Bucket=self._bucket, MaxKeys=1)

    def head(self, key: str) -> ObjectInfo | None:
        """Return the object's size, or None if it does not exist.

        A HEAD response has no body, so a missing bucket also gives None; `check_access` (or
        `read`) tells the two apart.
        """
        with translated("head"):
            try:
                response = self._client.head_object(Bucket=self._bucket, Key=key)
            except ClientError as error:
                if is_missing(error):
                    return None
                raise
        return ObjectInfo(key, response["ContentLength"], response.get("LastModified"))

    def list_objects(self, prefix: str) -> tuple[ObjectInfo, ...]:
        """Return every object under the prefix (all pages), in key order."""
        paginator = self._client.get_paginator("list_objects_v2")
        with translated("list"):
            pages = list(paginator.paginate(Bucket=self._bucket, Prefix=prefix))
        entries = [entry for page in pages for entry in page.get("Contents", [])]
        found = [_listed_info(entry) for entry in entries]
        return tuple(sorted(found, key=attrgetter("key")))

    def read(self, key: str) -> bytes | None:
        """Return the object's body, or None if it does not exist."""
        with translated("read"):
            try:
                response = self._client.get_object(Bucket=self._bucket, Key=key)
            except ClientError as error:
                if is_missing(error):
                    return None
                raise
            body: bytes = response["Body"].read()
        return body

    def write(self, key: str, body: bytes, content_type: str) -> None:
        """Create or replace the object."""
        with translated("write"):
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
            )

    def write_new(self, key: str, body: bytes, content_type: str) -> bool:
        """Create the object with `If-None-Match: *`; return False if it already existed.

        Ambiguous: when botocore retries after a lost response to a PUT that was written, the
        retry gets 412 and this returns False although this call created the object. Callers
        that must know compare the stored content (`read`).
        """
        with translated("write"):
            try:
                self._client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=body,
                    ContentType=content_type,
                    IfNoneMatch="*",
                )
            except ClientError as error:
                if http_status(error) == HTTP_PRECONDITION_FAILED:
                    return False
                raise
        return True

    def copy(self, source_key: str, target_key: str) -> None:
        """Copy an object inside the bucket (multipart copy from `part_bytes` on).

        Raises:
            ObjectMissingError: The source object does not exist.
            StorageError: `r2_bucket_not_found` when the bucket itself is missing (the copy's
                HEAD cannot tell; one listing call on the error path does).
        """
        source: CopySourceTypeDef = {"Bucket": self._bucket, "Key": source_key}
        operation = "copy"
        missing = False
        with translated(operation):
            try:
                self._client.copy(source, self._bucket, target_key, Config=self._transfer)
            except ClientError as error:
                if not is_missing(error):
                    raise
                missing = True
        if missing:
            self.check_access()
            raise ObjectMissingError(operation)

    def delete(self, key: str) -> None:
        """Delete the object; a missing object is success."""
        with translated("delete"):
            try:
                self._client.delete_object(Bucket=self._bucket, Key=key)
            except ClientError as error:
                if not is_missing(error):
                    raise

    def abort_uploads(self, prefix: str) -> int:
        """Abort every unfinished multipart upload under the prefix; return how many were aborted."""
        with translated("abort uploads"):
            return abort_all(self._client, self._bucket, prefix)

    def count_uploads(self, prefix: str) -> int:
        """Return how many unfinished multipart uploads exist under the prefix."""
        with translated("list uploads"):
            return len(open_uploads(self._client, self._bucket, prefix))

    def upload_file(
        self,
        key: str,
        source: Path,
        content_type: str,
        progress: UploadProgress,
        cancel: "CancelToken",
    ) -> int:
        """Upload a local file, in parts from `part_bytes` on; return the bytes sent.

        `cancel` is checked before each part; a cancelled or failed multipart upload is
        aborted, and the next upload of the object starts from its beginning. A failure after a
        lost response may leave the object written (see `put_in_parts`): verify with `head`.

        Raises:
            CancelledError: The cancel token was set.
            StorageError: R2 refused or could not be reached.
            OSError: The source file could not be read.
        """
        target = UploadTarget(self._client, self._bucket, key, content_type)
        control = UploadControl(self._part_bytes, progress, cancel)
        with translated("upload"):
            if source.stat().st_size < self._part_bytes:
                return put_whole(target, source, control)
            return put_in_parts(target, source, control)


def _listed_info(entry: "ObjectTypeDef") -> ObjectInfo:
    return ObjectInfo(entry["Key"], entry["Size"], entry.get("LastModified"))
