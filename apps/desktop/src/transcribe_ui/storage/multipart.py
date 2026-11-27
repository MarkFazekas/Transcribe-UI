"""Upload of one local file with progress and cancellation between parts (plan m5 §1 "Upload").

Parts are sent one after the other. A cancelled or failed upload is aborted; parts are never
resumed across requests: an interrupted object is uploaded again from its start.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from botocore.exceptions import BotoCoreError, ClientError

from transcribe_ui.api.errors import CancelledError
from transcribe_ui.storage.constants import S3_NO_SUCH_UPLOAD
from transcribe_ui.storage.errors import error_code

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client
    from types_boto3_s3.type_defs import CompletedPartTypeDef, MultipartUploadTypeDef

    from transcribe_ui.api.ports import CancelToken

logger = logging.getLogger(__name__)

# Receives the number of bytes uploaded so far, after each part (and once for a single PUT).
type UploadProgress = Callable[[int], None]


@dataclass(frozen=True)
class UploadTarget:
    """Where and how one file is uploaded."""

    client: "S3Client"
    bucket: str
    key: str
    content_type: str


@dataclass(frozen=True)
class UploadControl:
    """How an upload reports progress and learns it should stop."""

    part_bytes: int
    progress: UploadProgress
    cancel: "CancelToken"

    def check_cancelled(self) -> None:
        """Stop when cancellation was requested.

        Raises:
            CancelledError: If the cancel token is set.
        """
        if self.cancel.is_cancelled():
            raise CancelledError


def put_whole(target: UploadTarget, source: Path, control: UploadControl) -> int:
    """Upload a file smaller than one part with a single PUT; return its size."""
    control.check_cancelled()
    body = source.read_bytes()
    target.client.put_object(
        Bucket=target.bucket,
        Key=target.key,
        Body=body,
        ContentType=target.content_type,
    )
    control.progress(len(body))
    return len(body)


def put_in_parts(target: UploadTarget, source: Path, control: UploadControl) -> int:
    """Upload a file as a multipart upload; return the bytes sent. Aborts the upload on failure."""
    created = target.client.create_multipart_upload(
        Bucket=target.bucket,
        Key=target.key,
        ContentType=target.content_type,
    )
    upload_id = created["UploadId"]
    try:
        parts, sent = _send_parts(target, upload_id, source, control)
        _complete(target, upload_id, parts)
    except Exception:
        _abort_quietly(target, upload_id)
        raise
    return sent


def _send_parts(
    target: UploadTarget,
    upload_id: str,
    source: Path,
    control: UploadControl,
) -> tuple[list["CompletedPartTypeDef"], int]:
    parts: list[CompletedPartTypeDef] = []
    sent = 0
    with source.open("rb") as stream:
        while True:
            control.check_cancelled()
            chunk = stream.read(control.part_bytes)
            if not chunk:
                break
            number = len(parts) + 1
            response = target.client.upload_part(
                Bucket=target.bucket,
                Key=target.key,
                UploadId=upload_id,
                PartNumber=number,
                Body=chunk,
            )
            parts.append({"ETag": response["ETag"], "PartNumber": number})
            sent += len(chunk)
            control.progress(sent)
    return parts, sent


def _complete(target: UploadTarget, upload_id: str, parts: list["CompletedPartTypeDef"]) -> None:
    target.client.complete_multipart_upload(
        Bucket=target.bucket,
        Key=target.key,
        UploadId=upload_id,
        MultipartUpload={"Parts": parts},
    )


def _abort_quietly(target: UploadTarget, upload_id: str) -> None:
    """Abort the multipart upload; a failure is only logged (`run_upload` aborts stale ones)."""
    try:
        target.client.abort_multipart_upload(Bucket=target.bucket, Key=target.key, UploadId=upload_id)
    except (BotoCoreError, ClientError) as error:
        logger.warning("Aborting the multipart upload of %s failed (%s)", target.key, type(error).__name__)


def abort_all(client: "S3Client", bucket: str, prefix: str) -> int:
    """Abort every unfinished multipart upload under the prefix; return how many were aborted."""
    paginator = client.get_paginator("list_multipart_uploads")
    pages = list(paginator.paginate(Bucket=bucket, Prefix=prefix))
    uploads = [upload for page in pages for upload in page.get("Uploads", [])]
    outcomes = [_abort_one(client, bucket, upload) for upload in uploads]
    return outcomes.count(True)


def _abort_one(client: "S3Client", bucket: str, upload: "MultipartUploadTypeDef") -> bool:
    """Abort one upload; return False if it was already gone."""
    try:
        client.abort_multipart_upload(
            Bucket=bucket,
            Key=upload["Key"],
            UploadId=upload["UploadId"],
        )
    except ClientError as error:
        if error_code(error) != S3_NO_SUCH_UPLOAD:
            raise
        return False
    return True
