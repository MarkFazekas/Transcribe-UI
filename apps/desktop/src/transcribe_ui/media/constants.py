"""Constants of the media tools (ffprobe/ffmpeg adapter, architecture §3.4)."""

from typing import Final

# Name of the ffmpeg executable (without the Windows `.exe`).
FFMPEG: Final = "ffmpeg"
# Name of the ffprobe executable (without the Windows `.exe`).
FFPROBE: Final = "ffprobe"
# Suffix of executables on Windows; the bundled build is looked up with it there.
WINDOWS_EXECUTABLE_SUFFIX: Final = ".exe"
# Executable extensions tried on Windows when `PATHEXT` is not set (its usual start).
DEFAULT_PATHEXT: Final = ".COM;.EXE;.BAT;.CMD"

# Longest `-version` of a bundled tool may take in the GUI's smoke mode (a first start under a
# virus scanner can be slow; the tool itself answers at once).
VERSION_TIMEOUT_S: Final = 30.0
# The option that makes ffmpeg and ffprobe print their version and exit.
VERSION_OPTION: Final = "-version"
# Longest a probe may run before the process is killed (a hang, not a slow file: ffprobe reads headers).
PROBE_TIMEOUT_S: Final = 120.0
# Longest one conversion attempt may run (reads the whole file; a long film on a slow drive).
CONVERT_TIMEOUT_S: Final = 3600.0
# Longest a subtitle extraction may run (it demuxes the whole file, like a conversion).
EXTRACT_TIMEOUT_S: Final = 3600.0
# Slowest read speed (bytes/s) the conversion and extraction limits allow for: a large file gets
# size / this when that is longer than the base limit (2 MB/s: an old USB 2 drive or a slow share).
SLOWEST_READ_BYTES_PER_S: Final = 2_000_000.0
# Largest container start time (s) whose cues are kept as stored; a later start (MPEG-TS, M2TS)
# is subtracted from the cues, as players count the time from the start of the file.
START_SHIFT_THRESHOLD_S: Final = 0.5
# Windows `CREATE_SUSPENDED`: the tool waits until it is inside its job object (not in `subprocess`).
WINDOWS_CREATE_SUSPENDED: Final = 0x4
# How often a running process is checked for the cancel token and its deadline.
POLL_INTERVAL_S: Final = 0.1
# How long to wait for a killed process to exit and close its pipes.
KILL_WAIT_S: Final = 10.0
# Characters of a tool's stderr kept in an error (its end, where ffmpeg puts the reason).
STDERR_TAIL_CHARS: Final = 500

# Arguments every ffmpeg call starts with: no stdin, no banner, errors only on stderr.
QUIET_ARGUMENTS: Final = ("-nostdin", "-hide_banner", "-loglevel", "error")
# ffprobe arguments: no banner, errors only on stderr, the container and every stream as JSON.
PROBE_ARGUMENTS: Final = (
    "-hide_banner",
    "-loglevel",
    "error",
    "-print_format",
    "json",
    "-show_format",
    "-show_streams",
)
# Prefix of an input or output path: the `file` protocol, so no file name is read as another protocol.
FILE_PROTOCOL: Final = "file:"
# ffmpeg output that writes to the process's stdout.
STDOUT_OUTPUT: Final = "pipe:1"
# Suffix of an output while ffmpeg writes it; renamed into place only once ffmpeg succeeded.
PARTIAL_SUFFIX: Final = ".part"
# Tries to rename a finished output into place (Windows: a scanner may hold a new file briefly).
REPLACE_ATTEMPTS: Final = 10
# Pause between those tries.
REPLACE_RETRY_S: Final = 0.2

# Extension of a stream-copied audio track (Matroska audio accepts practically every codec).
MATROSKA_EXTENSION: Final = "mka"
# ffmpeg muxer of MATROSKA_EXTENSION.
MATROSKA_MUXER: Final = "matroska"
# Extension of the lossless fallback when the Matroska muxer refuses the codec.
FLAC_EXTENSION: Final = "flac"
# ffmpeg muxer and encoder of FLAC_EXTENSION.
FLAC_CODEC: Final = "flac"
# ffmpeg output arguments of a conversion: audio only, no metadata (no titles or tags of the source).
AUDIO_ONLY_ARGUMENTS: Final = ("-vn", "-sn", "-dn", "-map_metadata", "-1")
# ffmpeg codec arguments of the stream copy into Matroska.
COPY_CODEC_ARGUMENTS: Final = ("-c", "copy")
# ffmpeg codec arguments of the FLAC fallback.
FLAC_CODEC_ARGUMENTS: Final = ("-c:a", FLAC_CODEC)
# ffmpeg muxer and encoder of an extracted subtitle.
SRT_FORMAT: Final = "srt"

# ffprobe `codec_type` of audio streams.
AUDIO_STREAM_TYPE: Final = "audio"
# ffprobe `codec_type` of video streams.
VIDEO_STREAM_TYPE: Final = "video"
# ffprobe `codec_type` of subtitle streams.
SUBTITLE_STREAM_TYPE: Final = "subtitle"
# Codec name used when ffprobe reports none (a codec ffmpeg does not know).
UNKNOWN_CODEC: Final = "unknown"
# Language tag meaning "undetermined" (ISO 639-2); reported as no language.
UNDETERMINED_LANGUAGE: Final = "und"
# Subtitle codecs ffmpeg can convert to SRT (architecture §3.4).
TEXT_SUBTITLE_CODECS: Final = frozenset(("subrip", "ass", "ssa", "mov_text", "webvtt", "text"))
# Image-based subtitle codecs: they can only be transcribed, never extracted.
BITMAP_SUBTITLE_CODECS: Final = frozenset(("hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"))

# Message of `media_tool_failed` from a probe.
PROBE_FAILED_MESSAGE: Final = "ffprobe could not read the file."
# Message of `media_tool_failed` when ffprobe succeeded but its JSON cannot be read.
PROBE_UNREADABLE_MESSAGE: Final = "ffprobe printed output that cannot be read."
# Message of `media_tool_failed` from a conversion (both attempts failed).
CONVERT_FAILED_MESSAGE: Final = "ffmpeg could not convert the audio track (stream copy and FLAC both failed)."
# Message of `media_tool_failed` from a subtitle extraction.
EXTRACT_FAILED_MESSAGE: Final = "ffmpeg could not extract the subtitle stream as SRT."
# Message of a tool that did not answer `-version` (the fake adapter's; the bundle check reports the exit).
VERSION_FAILED_MESSAGE: Final = "The tool did not answer -version."

# Codecs the fake adapter's "Matroska muxer" refuses, so its `convert` falls back to FLAC like the real one.
FAKE_REFUSED_CODECS: Final = frozenset(("adpcm_ima_qt",))
# Exit code the fake adapter reports for a failed tool call.
FAKE_FAILURE_EXIT_CODE: Final = 1
# Duration the fake adapter reports for a file without an entry.
FAKE_DEFAULT_DURATION_S: Final = 60.0

# Error code: ffmpeg or ffprobe cannot be found or started.
ERROR_MEDIA_TOOL_NOT_FOUND: Final = "media_tool_not_found"
# Error code: ffmpeg or ffprobe failed on the file; `details.stderr` has the end of its message.
ERROR_MEDIA_TOOL_FAILED: Final = "media_tool_failed"
# Error code: the converted audio cannot be written (output folder missing rights, disk full, ...).
ERROR_MEDIA_OUTPUT_UNWRITABLE: Final = "media_output_unwritable"
# Error code: a track or stream index is negative (the caller's bug; nothing was run).
ERROR_MEDIA_INDEX_INVALID: Final = "media_index_invalid"
# Error code: ffmpeg or ffprobe outlived its timeout and was killed; nothing was left behind.
ERROR_MEDIA_TOOL_TIMEOUT: Final = "media_tool_timeout"
