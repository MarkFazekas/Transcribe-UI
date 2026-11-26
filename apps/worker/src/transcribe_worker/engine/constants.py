"""Settings of the WhisperX transcription engine (architecture §5.3)."""

from typing import Final

# Engine name recorded in EngineInfo; also the distribution whose installed version is recorded.
ENGINE_NAME: Final = "whisperx"
# Whisper model (the `ctranslate2` conversion, resolved through the HF cache under HF_HOME).
MODEL: Final = "large-v3"
# Device of the Whisper and the alignment models.
DEVICE: Final = "cuda"
# Compute type of the Whisper model in `ctranslate2`.
COMPUTE_TYPE: Final = "float16"
# Voice activity detection of the batched pipeline (WhisperX defaults to `pyannote`).
VAD_METHOD: Final = "silero"
# Segments decoded together by the batched (normal mode) pipeline.
BATCH_SIZE: Final = 16

# Careful mode decodes one window at a time (faster-whisper's sequential transcribe).
CAREFUL_BATCH_SIZE: Final = 1
# Beam width of careful mode.
CAREFUL_BEAM_SIZE: Final = 5
# Temperatures tried in order when a careful decode fails the thresholds below.
CAREFUL_TEMPERATURES: Final = (0, 0.2, 0.4, 0.6, 0.8, 1.0)
# A decode whose text compresses better than this (gzip ratio) is treated as a repetition loop.
CAREFUL_COMPRESSION_RATIO_THRESHOLD: Final = 2.4
# A decode whose average token log probability is below this is retried at a higher temperature.
CAREFUL_LOG_PROB_THRESHOLD: Final = -1.0
# A window with a higher no-speech probability (and a failed log probability check) is silence.
CAREFUL_NO_SPEECH_THRESHOLD: Final = 0.6
# Careful mode does not feed the previous window's text as a prompt (stops hallucination chains).
CAREFUL_CONDITION_ON_PREVIOUS_TEXT: Final = False
# Careful mode filters non-speech with faster-whisper's bundled Silero VAD.
CAREFUL_VAD_FILTER: Final = True

# Start of the ValueError message WhisperX raises when a language has no default alignment model.
NO_ALIGN_MODEL_MESSAGE: Final = "No default align-model for language"
