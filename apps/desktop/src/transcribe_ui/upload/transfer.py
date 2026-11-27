"""Putting one audio object into R2 and verifying it by its size (architecture §3.3, §6).

The target key is deleted first, so a HEAD after an ambiguous failure can only see this
request's object: a retried `CompleteMultipartUpload` that gets `NoSuchUpload`, or a lost
response to a copy, may have written it although the call failed (§6). Such a failure counts as
success when HEAD finds the object with the expected size; otherwise the error stands and
re-sending the request uploads the object again from its start.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from transcribe_ui.api.ports import CancelToken
from transcribe_ui.storage.errors import StorageError
from transcribe_ui.storage.multipart import UploadProgress
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.constants import AUDIO_CONTENT_TYPE
from transcribe_ui.upload.errors import UploadUnverifiedError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalFile:
    """A local file to upload, with the size it had when it was checked."""

    path: Path
    size: int


def upload_object(
    store: ObjectStore, key: str, local: LocalFile, progress: UploadProgress, cancel: CancelToken
) -> None:
    """Upload a local file to `key`, then verify the object's size.

    Raises:
        CancelledError: Cancelled before a part (the multipart upload is aborted).
        StorageError: R2 failed and the object is not there.
        UploadUnverifiedError: The object is missing or has another size after the upload.
        OSError: The file could not be read.
    """
    store.delete(key)
    try:
        store.upload_file(key, local.path, AUDIO_CONTENT_TYPE, progress, cancel)
    except StorageError as error:
        _accept_if_stored(store, key, local.size, error)
        return
    _verify(store, key, local.size)


def copy_object(store: ObjectStore, source_key: str, key: str, size: int) -> None:
    """Copy another batch's object to `key` server side, then verify the copy's size.

    Raises:
        ObjectMissingError: The source object is gone (its audio was cleaned up).
        StorageError: R2 failed and the copy is not there.
        UploadUnverifiedError: The copy is missing or has another size.
    """
    store.delete(key)
    try:
        store.copy(source_key, key)
    except StorageError as error:
        _accept_if_stored(store, key, size, error)
        return
    _verify(store, key, size)


def _stored(store: ObjectStore, key: str, size: int) -> bool:
    found = store.head(key)
    return found is not None and found.size == size


def _accept_if_stored(store: ObjectStore, key: str, size: int, error: StorageError) -> None:
    """Take a failed write as done when the object is there with its size (a lost response); else re-raise."""
    if not _stored(store, key, size):
        raise error
    logger.info("A write of %s failed (%s) but the object is there; taking it as written", key, error.code)


def _verify(store: ObjectStore, key: str, size: int) -> None:
    if not _stored(store, key, size):
        raise UploadUnverifiedError(key)
