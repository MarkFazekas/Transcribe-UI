"""Silero VAD for WhisperX, loaded from the copy baked into the image (no network at run time).

WhisperX 3.8.6 `vads.silero.Silero` calls `torch.hub.load("snakers4/silero-vad")`, which
contacts GitHub on every engine start even when the hub cache exists; a refused request would
stop the engine. `LocalSilero` sets the same attributes from the baked repository
(`source="local"`) and is passed to `whisperx.load_model(vad_model=...)`.
"""

from pathlib import Path

import torch
from whisperx.vads.silero import Silero
from whisperx.vads.vad import Vad

from transcribe_worker.engine import constants


def silero_dir() -> Path:
    """Return the folder of the baked Silero repository ($TORCH_HOME/hub/...)."""
    return Path(torch.hub.get_dir()) / constants.SILERO_HUB_DIR_NAME


def load_silero() -> tuple[object, tuple[object, ...]]:
    """Load the Silero model and its helper functions from the baked repository."""
    loaded: tuple[object, tuple[object, ...]] = torch.hub.load(
        str(silero_dir()),
        constants.SILERO_HUB_MODEL,
        source="local",
        onnx=False,
    )
    return loaded


class LocalSilero(Silero):  # type: ignore[misc]  # whisperx has no type information
    """WhisperX's Silero VAD, built from the local copy instead of the GitHub hub."""

    def __init__(self, vad_onset: float, chunk_size: float) -> None:
        """Set what `Silero.__init__` sets, without its `torch.hub.load` from GitHub."""
        Vad.__init__(self, vad_onset)  # noqa: WPS609 - Silero.__init__ is skipped on purpose (see the class docstring)
        self.vad_onset = vad_onset
        self.chunk_size = chunk_size
        model, helpers = load_silero()
        self.vad_pipeline = model
        self.get_speech_timestamps = helpers[0]
        self.read_audio = helpers[2]
