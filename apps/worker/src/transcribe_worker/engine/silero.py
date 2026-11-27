"""Silero VAD for WhisperX, loaded from the copy baked into the image (no network at run time).

WhisperX 3.8.6 `vads.Silero.__init__` calls `torch.hub.load("snakers4/silero-vad", ...)`,
which contacts GitHub on every engine start even when the hub cache exists; a refused request
would stop the engine. `build_vad` builds WhisperX's own `Silero` (so nothing of it is
replicated here) while `torch.hub.load` is temporarily redirected to the baked repository
(`source="local"`). Engine start is single-threaded, so the temporary patch is safe.
"""

from collections.abc import Callable
from functools import partial
from pathlib import Path
from unittest.mock import patch

import torch
from whisperx.vads import Silero

from transcribe_worker.engine import constants


class VadLoadError(RuntimeError):
    """WhisperX asked the torch hub for something other than the baked Silero VAD."""


def silero_dir() -> Path:
    """Return the folder of the baked Silero repository ($TORCH_HOME/hub/...)."""
    return Path(torch.hub.get_dir()) / constants.SILERO_HUB_DIR_NAME


def build_vad() -> Silero:
    """Build WhisperX's Silero VAD (default WhisperX options) from the baked repository."""
    local_load = partial(_load_local, torch.hub.load)
    with patch.object(torch.hub, "load", new=local_load):
        return Silero(**constants.WHISPERX_VAD_OPTIONS)


def check_vad() -> list[object]:
    """Run the VAD as WhisperX's pipeline does (`asr.FasterWhisperPipeline.transcribe`) on silence.

    Used by the image smoke test: proves that the model loads offline and runs.
    """
    vad = build_vad()
    silence = torch.zeros(constants.SAMPLE_RATE_HZ * constants.VAD_CHECK_SECONDS)
    waveform = vad.preprocess_audio(silence)
    segments = vad({"waveform": waveform, "sample_rate": constants.SAMPLE_RATE_HZ})
    merged: list[object] = vad.merge_chunks(
        segments,
        constants.WHISPERX_VAD_OPTIONS["chunk_size"],
        onset=constants.WHISPERX_VAD_OPTIONS["vad_onset"],
        offset=constants.WHISPERX_VAD_OPTIONS["vad_offset"],
    )
    return merged


def _load_local(original: Callable[..., object], repo_or_dir: str, model: str, **options: object) -> object:
    """`torch.hub.load` stand-in: serve only the Silero VAD, from the local copy."""
    if (repo_or_dir, model) != (constants.SILERO_HUB_REPO, constants.SILERO_HUB_MODEL):
        msg = f"unexpected torch hub load of {repo_or_dir}:{model}"
        raise VadLoadError(msg)
    onnx = options.get("onnx", False)
    return original(str(silero_dir()), model, source="local", onnx=onnx)
