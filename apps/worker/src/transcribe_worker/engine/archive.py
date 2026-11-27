"""Pinned source archives for the image build: download with retries, check, unpack safely.

Standard library only (no GPU stack), so it is tested in CI. Used by `engine.bake` for the
Silero VAD repository.
"""

import hashlib
import io
import shutil
import tarfile
import tempfile
import time
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from transcribe_worker.engine.constants import (
    DOWNLOAD_ATTEMPTS,
    DOWNLOAD_RETRY_DELAY_S,
    DOWNLOAD_TIMEOUT_S,
)


class ArchiveError(RuntimeError):
    """A downloaded archive is not what was pinned, or cannot be unpacked safely."""


type Sleep = Callable[[float], None]


def fetch(url: str, sha256: str, sleep: Sleep = time.sleep) -> bytes:
    """Download `url`, retrying 429/5xx and connection errors, and check its SHA-256.

    Raises:
        ArchiveError: The download kept failing, or the checksum differs.
    """
    archive = _download_with_retries(url, sleep)
    digest = hashlib.sha256(archive).hexdigest()
    if digest != sha256:
        msg = f"archive SHA-256 {digest} is not the pinned {sha256}"
        raise ArchiveError(msg)
    return archive


def unpack_single_folder(archive: bytes, target: Path) -> None:
    """Unpack a .tar.gz holding exactly one top-level folder as `target` (replacing it).

    Members escaping the archive (absolute paths, `..`, links outside it) are refused.

    Raises:
        ArchiveError: The archive is unsafe or does not hold exactly one top-level folder.
    """
    with tempfile.TemporaryDirectory() as scratch:
        _extract(archive, Path(scratch))
        tops = list(Path(scratch).iterdir())
        if len(tops) != 1 or not tops[0].is_dir():
            msg = f"expected one top-level folder, found {sorted(top.name for top in tops)}"
            raise ArchiveError(msg)
        shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(tops[0], target)


def _extract(archive: bytes, destination: Path) -> None:
    try:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(destination, filter="data")
    except (tarfile.TarError, OSError) as error:
        msg = f"cannot unpack the archive safely: {error}"
        raise ArchiveError(msg) from error


def _download_with_retries(url: str, sleep: Sleep) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            return _download(url)
        except (HTTPError, URLError, TimeoutError) as error:
            if not _retryable(error):
                raise ArchiveError(str(error)) from error
            last_error = error
        if attempt < DOWNLOAD_ATTEMPTS:
            sleep(DOWNLOAD_RETRY_DELAY_S * attempt)
    msg = f"download of {url} failed {DOWNLOAD_ATTEMPTS} times: {last_error}"
    raise ArchiveError(msg)


def _download(url: str) -> bytes:
    with urlopen(url, timeout=DOWNLOAD_TIMEOUT_S) as response:  # noqa: S310 - pinned https URL
        body: bytes = response.read()
    return body


def _retryable(error: Exception) -> bool:
    if isinstance(error, HTTPError):
        return error.code == HTTPStatus.TOO_MANY_REQUESTS or error.code >= HTTPStatus.INTERNAL_SERVER_ERROR
    return True
