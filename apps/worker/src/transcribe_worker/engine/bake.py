"""Download the models into the image at build time (`python -m transcribe_worker.engine.bake`).

Runs on the CPU build machine with HF_HOME and TORCH_HOME set to the paths the worker uses,
so a pod starts without downloads for the baked languages (architecture §5.1): the Whisper
model, the alignment models of the default languages, and the Silero VAD repository at a
pinned commit, checked against its SHA-256 and unpacked where `engine.silero` loads it.
"""

import hashlib
import io
import logging
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.request import urlopen

import whisperx
from faster_whisper.utils import download_model

from transcribe_contracts.constants import DEFAULT_LANGUAGES
from transcribe_worker.engine import constants
from transcribe_worker.engine.silero import silero_dir

logger = logging.getLogger(__name__)


class BakeError(RuntimeError):
    """A downloaded file is not what was pinned."""


def bake() -> None:
    """Download every model the image ships with."""
    logger.info("downloading the Whisper model %s", constants.MODEL)
    download_model(constants.MODEL)
    for language in DEFAULT_LANGUAGES:
        logger.info("downloading the %s alignment model", language)
        whisperx.load_align_model(language_code=language, device="cpu")
    logger.info("downloading Silero VAD %s (%s)", constants.SILERO_RELEASE_TAG, constants.SILERO_COMMIT)
    unpack_silero(fetch_silero(), silero_dir())


def fetch_silero() -> bytes:
    """Download the pinned Silero archive and check its SHA-256.

    Raises:
        BakeError: The archive does not match the pinned checksum.
    """
    with urlopen(constants.SILERO_ARCHIVE_URL, timeout=constants.DOWNLOAD_TIMEOUT_S) as response:  # noqa: S310 - fixed https URL
        archive: bytes = response.read()
    digest = hashlib.sha256(archive).hexdigest()
    if digest != constants.SILERO_ARCHIVE_SHA256:
        msg = f"Silero archive SHA-256 {digest} is not the pinned {constants.SILERO_ARCHIVE_SHA256}"
        raise BakeError(msg)
    return archive


def unpack_silero(archive: bytes, target: Path) -> None:
    """Unpack the archive's single top folder as `target` (replacing it)."""
    with tempfile.TemporaryDirectory() as scratch:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(scratch, filter="data")
        top = next(Path(scratch).iterdir())
        shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(top, target)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bake()
    sys.exit(0)
