"""Constants of the placement planner (architecture §3.5)."""

import codecs
import os
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from transcribe_ui.placement.classes import SubtitleState

# Extension of every placed subtitle file (lower case, without the dot).
SUBTITLE_EXTENSION: Final = "srt"
# Joins folder names and the stem in flat names (`C_D_e.hu.srt`), and the stem and the source
# extension in same-folder collisions (`movie_mp4.hu.srt`).
NAME_JOINER: Final = "_"
# Replaces each character a target file system does not allow in a name.
REPLACEMENT_CHARACTER: Final = "_"
# Longest file or folder name: UTF-8 bytes on POSIX systems, UTF-16 code units on Windows.
MAX_NAME_LENGTH: Final = 255
# Bytes per UTF-16 code unit (Windows counts name lengths in them).
UTF16_UNIT_BYTES: Final = 2
# Longest full target path on Windows (UTF-16 code units): MAX_PATH 260 minus the final NUL.
WINDOWS_MAX_PATH_LENGTH: Final = 259
# Longest folder path Windows creates (UTF-16 code units): 248 (MAX_PATH minus an 8.3 name)
# minus the final NUL.
WINDOWS_MAX_FOLDER_LENGTH: Final = 247
# Suffix of the temp file a subtitle is written to before it is moved into place (`run_download`).
TEMP_SUFFIX: Final = ".tmp"
# Hex characters of the random token that makes each temp file's name unique (`<name>.<token>.tmp`).
TEMP_TOKEN_LENGTH: Final = 6
# Characters a temp file adds to its target's name (`.<token>.tmp`, ASCII); name and path limits
# keep room for them.
TEMP_TAIL_LENGTH: Final = 1 + TEMP_TOKEN_LENGTH + len(TEMP_SUFFIX)
# Open flag of binary mode where the OS has one (Windows), else nothing.
OPEN_BINARY: Final[int] = getattr(os, "O_BINARY", 0)
# Open flag that refuses to follow a link at the path, where the OS has one (POSIX), else nothing.
OPEN_NO_FOLLOW: Final[int] = getattr(os, "O_NOFOLLOW", 0)
# Open flag that never waits for a writer (a FIFO), where the OS has one (POSIX), else nothing.
OPEN_NON_BLOCKING: Final[int] = getattr(os, "O_NONBLOCK", 0)
# Open flags of a file created here and now for writing (fails if anything is at the path).
NEW_FILE_FLAGS: Final = os.O_WRONLY | os.O_CREAT | os.O_EXCL
# Flags of a new temp file: a new file, never through a link, binary on Windows.
TEMP_FLAGS: Final = NEW_FILE_FLAGS | OPEN_BINARY | OPEN_NO_FOLLOW
# Permissions of a new temp file before the umask, as for any new file.
TEMP_MODE: Final = 0o666
# Seconds after which a temp file of a target counts as left by a killed download and is removed.
STALE_TEMP_S: Final = 600
# Most rounds of collision renaming; each round changes at least one name, so it ends earlier.
MAX_COLLISION_ROUNDS: Final = 64
# Outcomes of a placement that write a file.
WRITING_OUTCOMES: Final = frozenset(("place", "overwrite"))
# Strategies that tell colliding files of different folders apart by folder names.
FLAT_STRATEGIES: Final = frozenset(("flat", "flat_with_folders"))
# Folders closest to the file joined into the name by flat_with_folders unless given (X).
DEFAULT_FOLDER_COUNT: Final = 1
# Version of the exported custom plan format.
CUSTOM_PLAN_VERSION: Final = 1

# Characters Windows does not allow in a name besides the control characters (`:` would also
# open an alternate data stream, `name:stream`).
WINDOWS_FORBIDDEN_CHARACTERS: Final = frozenset('<>:"/|?*\N{REVERSE SOLIDUS}')
# Highest code point of the control characters Windows does not allow in a name.
WINDOWS_LAST_CONTROL_CHARACTER: Final = 0x1F
# Characters POSIX systems do not allow in a name.
POSIX_FORBIDDEN_CHARACTERS: Final = frozenset("/\0")
# Characters Windows drops from the end of a name, so a name may not end with them.
WINDOWS_TRAILING_CHARACTERS: Final = ". "
# Device names Windows reserves (NUL and the others), upper case; also reserved with any extension (`CON.hu.srt`).
WINDOWS_RESERVED_NAMES: Final = frozenset(
    (
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        *(f"COM{number}" for number in "0123456789¹²³"),
        *(f"LPT{number}" for number in "0123456789¹²³"),
    ),
)

# Plan error: a target file name is longer than the target file system allows.
ERROR_NAME_TOO_LONG: Final = "name_too_long"
# Plan error: a full target path is longer than Windows allows.
ERROR_PATH_TOO_LONG: Final = "path_too_long"
# Plan error: two jobs would be placed at the same target.
ERROR_DUPLICATE_TARGET: Final = "duplicate_target"
# Plan error: what is at the target cannot be read (no permission), so it cannot be planned for.
ERROR_TARGET_UNREADABLE: Final = "target_unreadable"
# Plan error: the scan root is not an absolute path or contains `..`.
ERROR_SCAN_ROOT_INVALID: Final = "scan_root_invalid"
# Plan error: a source path is not an absolute path or contains `..`.
ERROR_SOURCE_PATH_INVALID: Final = "source_path_invalid"
# Plan error: the target exists and is not a file, so it cannot be overwritten or skipped as one.
ERROR_TARGET_NOT_A_FILE: Final = "target_not_a_file"
# Plan error: a source file is not below the scan root the strategy counts folders from.
ERROR_OUTSIDE_SCAN_ROOT: Final = "outside_scan_root"
# Plan error: the output folder is not an absolute path.
ERROR_OUTPUT_NOT_ABSOLUTE: Final = "output_not_absolute"
# Custom plan error: the plan file is not valid JSON of the custom plan schema.
ERROR_CUSTOM_SCHEMA: Final = "custom_plan_schema"
# Custom plan error: a job is listed more than once.
ERROR_CUSTOM_DUPLICATE_JOB: Final = "custom_plan_duplicate_job"
# Custom plan error: a listed job is not one of the download's jobs.
ERROR_CUSTOM_UNKNOWN_JOB: Final = "custom_plan_unknown_job"
# Custom plan error: one of the download's jobs is not listed.
ERROR_CUSTOM_MISSING_JOB: Final = "custom_plan_missing_job"
# Custom plan error: a job's source path or original folder was changed.
ERROR_CUSTOM_SOURCE_CHANGED: Final = "custom_plan_source_changed"
# Custom plan error: an output file or folder name is not valid on this OS.
ERROR_CUSTOM_INVALID_NAME: Final = "custom_plan_invalid_name"
# Custom plan error: an output file name does not end with `.srt`.
ERROR_CUSTOM_NOT_SRT: Final = "custom_plan_not_srt"
# Custom plan error: a target is outside the chosen output folder after resolving `..` and links.
ERROR_CUSTOM_OUTSIDE_ROOT: Final = "custom_plan_outside_root"

# Download (`plan_download`, `run_download`, the custom plan requests; plan m5 §1 "Download").

# Batch phases whose subtitles can be downloaded: the finished jobs of a `ready` batch, again and
# again while the results are in R2, and the extracted subtitles of a `cleaned` batch (they are
# kept in SQLite).
DOWNLOAD_PHASES: Final = frozenset(("ready", "transcribed", "downloaded", "cleaned"))
# Batch phase whose transcribed subtitles are gone from R2: only its extracted ones are placed.
CLEANED_PHASE: Final = "cleaned"
# Batch phase of a batch whose every job has a placement outcome.
DOWNLOADED_PHASE: Final = "downloaded"
# Outcomes recorded for a job: each counts towards `downloaded` (`not_finished` is no outcome).
RECORDED_OUTCOMES: Final = frozenset(
    ("place", "overwrite", "skip_existing", "skip_folder_missing", "skip_empty", "skip_failed"),
)
# Job kind of an embedded subtitle extracted locally (its SRT is in SQLite).
EXTRACT_KIND: Final = "extract"
# Subtitle state of a `transcribe` job by its status; any other status (`pending`) has no result yet.
TRANSCRIBE_SUBTITLES: Final[Mapping[str, "SubtitleState"]] = MappingProxyType(
    {"ok": "available", "no_speech": "empty", "failed": "failed"},
)
# Request field that confirms overwriting existing files (`run_download`, `import_custom_plan`).
CONFIRM_OVERWRITE_FIELD: Final = "confirm_overwrite"
# Progress stage of a download: one event per job.
DOWNLOAD_STAGE: Final = "download"
# Encoding of the extracted SRT texts written to disk.
SUBTITLE_ENCODING: Final = "utf-8"
# Largest custom plan file read by `import_custom_plan` (32 MiB; a plan has a few hundred bytes per job).
MAX_CUSTOM_PLAN_BYTES: Final = 33_554_432
# Reason of `custom_plan_unreadable` for a plan path that is not a regular file (a folder, a FIFO).
NOT_A_FILE: Final = "not_a_file"
# Reason of `custom_plan_unreadable` for a plan file over `MAX_CUSTOM_PLAN_BYTES`.
TOO_LARGE: Final = "too_large"
# Flags of the plan file's open: read only, never waiting for a writer (a FIFO), binary on Windows.
PLAN_READ_FLAGS: Final = os.O_RDONLY | OPEN_NON_BLOCKING | OPEN_BINARY
# The UTF-8 byte order mark some Windows editors put at the start of a saved file.
BOM_UTF8: Final = codecs.BOM_UTF8
# The byte order mark as text (a UTF-8 file decoded without `utf-8-sig` keeps it).
BYTE_ORDER_MARK: Final = "\N{ZERO WIDTH NO-BREAK SPACE}"
# Indentation of the custom plan file written by `export_custom_plan`.
CUSTOM_PLAN_INDENT: Final = 2

# Error code: the placement plan has errors, so nothing was written (details: the plan errors).
ERROR_DOWNLOAD_PLAN_INVALID: Final = "download_plan_invalid"
# Failure code of a job whose subtitle could not be written (the other jobs go on; send the request again).
ERROR_DOWNLOAD_WRITE_FAILED: Final = "download_write_failed"
# Failure code of a finished job whose subtitle is not in R2 (the other jobs go on).
ERROR_SUBTITLE_MISSING: Final = "subtitle_missing"
# Error code: the custom plan file cannot be read.
ERROR_CUSTOM_PLAN_UNREADABLE: Final = "custom_plan_unreadable"
# Error code: the custom plan file cannot be written.
ERROR_CUSTOM_PLAN_UNWRITABLE: Final = "custom_plan_unwritable"
