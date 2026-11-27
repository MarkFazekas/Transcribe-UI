"""Constants of the placement planner (architecture §3.5)."""

from typing import Final

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
# Suffix of the temp file a subtitle is written to before `os.replace` moves it into place
# (`run_download`); name and path limits keep room for it.
TEMP_SUFFIX: Final = ".tmp"
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
