"""Constants of the transcription engines (WhisperX settings, architecture §5.3), the engine process and the fake."""

from typing import Final

# Engine process message: models loaded; payload = settings of normal and careful mode.
MESSAGE_READY: Final = "ready"
# Engine process message: the engine could not be built; payload = error text.
MESSAGE_START_FAILED: Final = "start_failed"
# Engine process message: a job finished; payload = the transcript as a dict.
MESSAGE_OK: Final = "ok"
# Engine process message: a job failed; payload = error text.
MESSAGE_ERROR: Final = "error"
# Seconds the engine process gets to exit before it is killed.
STOP_TIMEOUT_S: Final = 30.0

# Fake engine: prefix of a scripted fake audio file (anything else is "speech").
FAKE_SCRIPT_PREFIX: Final = b"FAKE:"
# Fake engine: seconds per word of the generated word times.
FAKE_WORD_S: Final = 0.4
# Fake engine: text of an unscripted file (real audio in the image smoke test).
FAKE_DEFAULT_TEXT: Final = "This is a fake transcript."
# Fake engine: segment repeated by the "loop" script in normal mode.
FAKE_LOOP_TEXT: Final = "Thank you for watching."
# Fake engine: how often the "loop" script repeats its segment.
FAKE_LOOP_COUNT: Final = 12
# Fake engine: exit code of the "crash" script (simulates a native crash).
FAKE_CRASH_EXIT_CODE: Final = 70
# Fake engine: environment variable naming a file whose existence makes the engine fail to start.
FAKE_BROKEN_FLAG_ENV: Final = "TRANSCRIBE_FAKE_BROKEN_FLAG"
# Fake engine: seconds after its answer at which the "quit" script ends the engine process.
FAKE_QUIT_DELAY_S: Final = 0.1
# Fake engine: seconds the "hang" script sleeps.
FAKE_HANG_S: Final = 3600.0

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
# Careful-mode VAD: split at pauses of 500 ms (default 2 s) so music between lines is cut out
# (research/whisper.md §4); 400 ms padding keeps word edges.
CAREFUL_VAD_MIN_SILENCE_MS: Final = 500
# Padding around each detected speech region in careful mode.
CAREFUL_VAD_SPEECH_PAD_MS: Final = 400

# Start of the ValueError message WhisperX raises when a language has no default alignment model.
NO_ALIGN_MODEL_MESSAGE: Final = "No default align-model for language"

# Torch hub repository WhisperX loads its Silero VAD from (baked into the image's hub cache).
SILERO_HUB_REPO: Final = "snakers4/silero-vad"
# Torch hub entry point of the Silero VAD.
SILERO_HUB_MODEL: Final = "silero_vad"
