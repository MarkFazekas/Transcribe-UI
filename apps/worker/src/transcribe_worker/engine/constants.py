"""Constants of the transcription engines and the engine process."""

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
# Fake engine: seconds the "hang" script sleeps.
FAKE_HANG_S: Final = 3600.0
