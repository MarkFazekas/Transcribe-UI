"""Download the models into the image at build time (`python -m transcribe_worker.engine.bake`).

Runs on the CPU build machine with HF_HOME and TORCH_HOME set to the paths the worker uses,
so a pod starts without downloads for the baked languages (architecture §5.1): the Whisper
model, the alignment models of the default languages, and the Silero VAD repository at a
pinned commit, checked against its SHA-256 and unpacked where `engine.silero` loads it.
"""

import logging
import sys

import whisperx
from faster_whisper.utils import download_model

from transcribe_contracts.constants import DEFAULT_LANGUAGES
from transcribe_worker.engine import constants
from transcribe_worker.engine.archive import fetch, unpack_single_folder
from transcribe_worker.engine.silero import silero_dir

logger = logging.getLogger(__name__)


def bake() -> None:
    """Download every model the image ships with."""
    logger.info("downloading the Whisper model %s", constants.MODEL)
    download_model(constants.MODEL)
    for language in DEFAULT_LANGUAGES:
        logger.info("downloading the %s alignment model", language)
        whisperx.load_align_model(language_code=language, device="cpu")
    logger.info("downloading Silero VAD %s (%s)", constants.SILERO_RELEASE_TAG, constants.SILERO_COMMIT)
    archive = fetch(constants.SILERO_ARCHIVE_URL, constants.SILERO_ARCHIVE_SHA256)
    unpack_single_folder(archive, silero_dir())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bake()
    sys.exit(0)
