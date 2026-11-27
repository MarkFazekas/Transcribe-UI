"""WhisperX transcription engine; needs the worker's `engine` extra and a CUDA GPU.

One of the engine adapter modules (`whisperx_engine`, `silero`, `bake`) that import the GPU
stack (`whisperx`, `torch`, `ctranslate2`); they are imported in the worker image only. Model
weights resolve through `HF_HOME` (the Whisper model, loaded with `local_files_only`, so it
never contacts Hugging Face; HF aligners) and `TORCH_HOME` (torchaudio aligners, and the
Silero VAD baked as a local hub repository and built through `engine.silero.build_vad`, so the
engine start never contacts GitHub), which the image sets. Aligners of languages that are not
baked are downloaded on first use; if that fails the job runs unaligned.

Normal mode runs WhisperX's batched pipeline (WhisperX 3.8.6 `asr.py`
`generate_segment_batched`), which decodes each speech chunk once: it reads `beam_size` from
the options but no temperatures or fallback thresholds. Careful mode therefore runs
faster-whisper's sequential `WhisperModel.transcribe` on the same `ctranslate2` model, the one
path with beam search and temperature fallback. Both modes then align words with the
language's default WhisperX alignment model when there is one, one segment at a time: a
segment the aligner returns nothing for (WhisperX drops segments whose times stay NaN) is kept
with its transcription times and no words, so no text is lost.
"""

import gc
import logging
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Protocol

import ctranslate2
import torch
import whisperx
from whisperx.asr import WhisperModel

from transcribe_contracts.classes import EngineInfo
from transcribe_worker.engine import constants
from transcribe_worker.engine.classes import Transcript
from transcribe_worker.engine.convert import build_transcript, same_text
from transcribe_worker.engine.silero import build_vad

logger = logging.getLogger(__name__)

type RawSegments = list[dict[str, object]]


class DecodedSegment(Protocol):
    """The fields used of a faster-whisper `Segment`."""

    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Aligner:
    """A loaded WhisperX alignment model and its metadata."""

    model: object
    metadata: object


def assert_gpu() -> None:
    """Fail loudly unless `ctranslate2` and `torch` see a CUDA device (instead of using the CPU).

    Raises:
        RuntimeError: No CUDA device is visible to `ctranslate2` or to `torch`.
    """
    if ctranslate2.get_cuda_device_count() < 1:
        msg = "ctranslate2 sees no CUDA device: check the GPU, the driver and LD_LIBRARY_PATH (cuBLAS, cuDNN)"
        raise RuntimeError(msg)
    if not torch.cuda.is_available():
        msg = "torch sees no CUDA device: check the GPU and that the driver supports the cu128 wheels"
        raise RuntimeError(msg)


class WhisperXTranscriber:
    """Transcriber backed by WhisperX `large-v3` on the GPU (see the module docstring)."""

    def __init__(self) -> None:
        """Check the GPU, then load the Whisper model and the batched pipeline around it.

        Raises:
            RuntimeError: No CUDA device is visible to `ctranslate2` or to `torch`.
        """
        assert_gpu()
        self._version = version(constants.ENGINE_NAME)
        # One model, shared by the batched pipeline and the careful (sequential) path.
        self._model = WhisperModel(
            constants.MODEL,
            device=constants.DEVICE,
            compute_type=constants.COMPUTE_TYPE,
            local_files_only=True,  # baked into the image; never contacts Hugging Face
        )
        self._pipeline = whisperx.load_model(
            constants.MODEL,
            constants.DEVICE,
            compute_type=constants.COMPUTE_TYPE,
            vad_model=build_vad(),
            vad_options=dict(constants.WHISPERX_VAD_OPTIONS),
            model=self._model,
        )
        self._aligner: Aligner | None = None
        self._aligner_language: str | None = None

    def settings(self, *, careful: bool) -> EngineInfo:
        """Return the settings recorded with every result of the given mode."""
        return EngineInfo(
            name=constants.ENGINE_NAME,
            version=self._version,
            model=constants.MODEL,
            compute_type=constants.COMPUTE_TYPE,
            batch_size=constants.CAREFUL_BATCH_SIZE if careful else constants.BATCH_SIZE,
        )

    def transcribe(self, audio: Path, language: str, *, careful: bool) -> Transcript:
        """Transcribe a 16 kHz mono WAV file in the given (forced) language."""
        # The aligner first: a language switch fails or falls back before minutes of decoding.
        aligner = self._aligner_for(language)
        samples = whisperx.load_audio(str(audio))
        decode = self._careful_segments if careful else self._batched_segments
        segments = decode(samples, language)
        if aligner is None:
            return build_transcript(segments, language, aligned=False)
        aligned = [part for segment in segments for part in _align(segment, aligner, samples)]
        return build_transcript(aligned, language, aligned=True)

    def _batched_segments(self, samples: object, language: str) -> RawSegments:
        transcribed: dict[str, RawSegments] = self._pipeline.transcribe(
            samples,
            batch_size=constants.BATCH_SIZE,
            language=language,
        )
        return transcribed["segments"]

    def _careful_segments(self, samples: object, language: str) -> RawSegments:
        segments, _ = self._model.transcribe(
            samples,
            language=language,
            beam_size=constants.CAREFUL_BEAM_SIZE,
            temperature=constants.CAREFUL_TEMPERATURES,
            compression_ratio_threshold=constants.CAREFUL_COMPRESSION_RATIO_THRESHOLD,
            log_prob_threshold=constants.CAREFUL_LOG_PROB_THRESHOLD,
            no_speech_threshold=constants.CAREFUL_NO_SPEECH_THRESHOLD,
            condition_on_previous_text=constants.CAREFUL_CONDITION_ON_PREVIOUS_TEXT,
            vad_filter=constants.CAREFUL_VAD_FILTER,
            vad_parameters={
                "min_silence_duration_ms": constants.CAREFUL_VAD_MIN_SILENCE_MS,
                "speech_pad_ms": constants.CAREFUL_VAD_SPEECH_PAD_MS,
            },
        )
        # `segments` is a lazy generator: decoding happens while it is consumed here.
        return [_as_mapping(segment) for segment in segments]

    def _aligner_for(self, language: str) -> Aligner | None:
        """Return the alignment model of the language, or None when WhisperX has none for it."""
        if self._aligner_language != language:
            self._release_aligner()
            try:
                self._aligner = _load_aligner(language)
            except Exception:
                # A download, disk or memory problem: this job runs unaligned, the next retries.
                logger.exception("could not load the %s alignment model; transcribing unaligned", language)
                return None
            self._aligner_language = language
        return self._aligner

    def _release_aligner(self) -> None:
        """Drop the current alignment model and free its GPU memory before another is loaded."""
        self._aligner_language = None
        if self._aligner is None:
            return
        self._aligner = None
        gc.collect()
        torch.cuda.empty_cache()


def _as_mapping(segment: DecodedSegment) -> dict[str, object]:
    """Return a faster-whisper segment as the mapping WhisperX uses for segments."""
    return {"start": segment.start, "end": segment.end, "text": segment.text}


def _align(segment: dict[str, object], aligner: Aligner, samples: object) -> RawSegments:
    """Return the aligned parts of one segment, or the segment itself when none came back."""
    aligned: dict[str, RawSegments] = whisperx.align(
        [segment],
        aligner.model,
        aligner.metadata,
        samples,
        constants.DEVICE,
        return_char_alignments=False,
    )
    parts = aligned["segments"]
    if not same_text(parts, [segment]):
        # WhisperX drops parts whose times stay NaN; keep the whole text, unaligned, instead.
        return [segment]
    return parts


def _load_aligner(language: str) -> Aligner | None:
    """Load the default WhisperX alignment model of the language.

    Returns None when WhisperX has none for the language.

    Raises:
        Exception: Loading failed (download, disk, memory); the caller transcribes unaligned.
    """
    try:
        model, metadata = whisperx.load_align_model(language_code=language, device=constants.DEVICE)
    except ValueError as error:
        if not str(error).startswith(constants.NO_ALIGN_MODEL_MESSAGE):
            raise
        return None
    return Aligner(model=model, metadata=metadata)
