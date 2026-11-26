"""Constants of the worker pipeline (architecture §5.3)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

# Environment variable: ULID of this run, chosen by the desktop app (generated when missing).
ENV_RUN_ID: Final = "TRANSCRIBE_RUN_ID"
# Environment variable: R2 S3 API endpoint (`https://<account>.r2.cloudflarestorage.com`).
ENV_R2_ENDPOINT_URL: Final = "R2_ENDPOINT_URL"
# Environment variable: R2 bucket holding every batch.
ENV_R2_BUCKET: Final = "R2_BUCKET"
# Environment variable: R2 access key id (a Runpod secret).
ENV_R2_ACCESS_KEY_ID: Final = "R2_ACCESS_KEY_ID"
# Environment variable: R2 secret access key (a Runpod secret).
ENV_R2_SECRET_ACCESS_KEY: Final = "R2_SECRET_ACCESS_KEY"  # noqa: S105 - a variable name, not a secret
# Environment variable: folder on the Global Volume that receives a copy of every result.
ENV_BACKUP_DIR: Final = "TRANSCRIBE_BACKUP_DIR"
# Environment variable: scratch folder on the container disk (audio cache, decoded audio, log).
ENV_WORK_DIR: Final = "TRANSCRIBE_WORK_DIR"
# Environment variable: size limit of the downloaded-audio cache in GB.
ENV_AUDIO_CACHE_GB: Final = "TRANSCRIBE_AUDIO_CACHE_GB"
# Environment variable: engine to load ("whisperx"; "fake" for the image smoke test).
ENV_ENGINE: Final = "TRANSCRIBE_ENGINE"

# Default backup folder: the Global Volume mount.
DEFAULT_BACKUP_DIR: Final = "/workspace/backups"
# Default scratch folder on the container disk.
DEFAULT_WORK_DIR: Final = "/tmp/transcribe-worker"  # noqa: S108 - container-local scratch, single user
# Default audio cache limit in GB (60 GB container disk, ~15 GB image, room for decoded audio).
DEFAULT_AUDIO_CACHE_GB: Final = 20.0
# Default engine.
DEFAULT_ENGINE: Final = "whisperx"
# Engine name -> "module:callable" building the Transcriber inside the engine process.
ENGINE_FACTORIES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "whisperx": "transcribe_worker.engine.whisperx_engine:WhisperXTranscriber",
        "fake": "transcribe_worker.engine.fake:FakeTranscriber",
    },
)
# Pod id recorded in the run status outside Runpod.
LOCAL_POD_ID: Final = "local"
# Bytes per GB for the cache limit.
BYTES_PER_GB: Final = 1024**3

# R2 region name (R2 ignores it, botocore needs one).
R2_REGION: Final = "auto"
# Attempts per S3 request (botocore "standard" retry mode, with backoff).
S3_MAX_ATTEMPTS: Final = 5
# Seconds to wait for a connection to R2 (bounded, so an outage cannot block a billed pod).
S3_CONNECT_TIMEOUT_S: Final = 10
# Seconds to wait for data on an open R2 connection.
S3_READ_TIMEOUT_S: Final = 30
# Transfers of at least this many bytes (64 MiB) use multipart, in parts of this size.
S3_MULTIPART_BYTES: Final = 67_108_864
# HTTP status of a conditional PUT whose object already exists.
HTTP_PRECONDITION_FAILED: Final = 412
# S3 error codes meaning "no such object".
S3_MISSING_CODES: Final = frozenset(("404", "NoSuchKey", "NotFound"))
# Content type of JSON objects.
JSON_CONTENT_TYPE: Final = "application/json"
# Content type of subtitle objects.
SRT_CONTENT_TYPE: Final = "application/x-subrip; charset=utf-8"
# Content type of the run log.
LOG_CONTENT_TYPE: Final = "application/x-ndjson; charset=utf-8"

# Decoded audio sample rate expected by Whisper.
SAMPLE_RATE_HZ: Final = 16000
# Upper bound for one ffmpeg decode.
DECODE_TIMEOUT_S: Final = 1800
# Characters of the ffmpeg error output kept in an error message.
DECODE_ERROR_CHARS: Final = 500
# Audio files downloaded and decoded ahead of the one being transcribed.
PREFETCH_DEPTH: Final = 2
# Seconds the prefetch thread waits for queue space before checking for a stop request.
PREFETCH_PUT_POLL_S: Final = 0.5

# Shortest per-job transcription timeout.
JOB_TIMEOUT_MIN_S: Final = 10 * 60
# Per-job timeout in seconds per second of audio in normal mode (see CAREFUL_TIMEOUT_FACTOR).
JOB_TIMEOUT_PER_AUDIO_S: Final = 1.0
# Timeout factor of careful mode relative to normal mode.
CAREFUL_TIMEOUT_FACTOR: Final = 2.0
# Time allowed for the engine process to load the models.
ENGINE_START_TIMEOUT_S: Final = 1200

# Seconds between heartbeats (status.json and log upload).
HEARTBEAT_INTERVAL_S: Final = 30.0
# Seconds to wait for a background thread (heartbeat, prefetch) to stop at the end of a run.
THREAD_STOP_TIMEOUT_S: Final = 60.0
# Log file name in the work dir (uploaded as runs/<run_id>/worker.log).
LOG_FILE_NAME: Final = "worker.log"
# Exit code of a run that ended normally (failed jobs included).
EXIT_OK: Final = 0
# Exit code of a run that could not work (configuration, storage or engine start failure).
EXIT_FAILED: Final = 1
