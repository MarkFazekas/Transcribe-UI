"""Download the models into the image at build time (`python -m transcribe_worker.engine.bake`).

Runs on the CPU build machine with HF_HOME and TORCH_HOME set to the paths the worker uses,
so a pod starts without downloads for the baked languages (architecture §5.1): the Whisper
model, the alignment models of the default languages and the Silero VAD WhisperX loads from
the torch hub (same call as WhisperX, so it finds the cache).
"""

import logging
import sys

import torch
import whisperx
from faster_whisper.utils import download_model

from transcribe_contracts.constants import DEFAULT_LANGUAGES
from transcribe_worker.engine import constants

logger = logging.getLogger(__name__)


def bake() -> None:
    """Download every model the image ships with."""
    logger.info("downloading the Whisper model %s", constants.MODEL)
    download_model(constants.MODEL)
    for language in DEFAULT_LANGUAGES:
        logger.info("downloading the %s alignment model", language)
        whisperx.load_align_model(language_code=language, device="cpu")
    logger.info("downloading the Silero VAD (torch hub)")
    torch.hub.load(
        repo_or_dir=constants.SILERO_HUB_REPO,
        model=constants.SILERO_HUB_MODEL,
        force_reload=False,
        onnx=False,
        trust_repo=True,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bake()
    sys.exit(0)
