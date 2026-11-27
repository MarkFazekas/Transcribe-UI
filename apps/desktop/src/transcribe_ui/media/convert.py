"""Converting one audio track of a video to its own file (architecture §3.4).

The track is stream-copied into Matroska audio, never re-encoded. If that fails (in practice: the
Matroska muxer refuses the codec), it is encoded to lossless FLAC instead. ffmpeg writes to a
`<name>.<ext>.<random>.part` file that is renamed into place only on success and removed on any
failure, timeout or cancel, so a finished name always means a complete file.
"""

import logging
import os
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from transcribe_ui.api.errors import DomainError
from transcribe_ui.media.classes import AudioExtension, ConvertedAudio
from transcribe_ui.media.constants import (
    AUDIO_ONLY_ARGUMENTS,
    CONVERT_FAILED_MESSAGE,
    COPY_CODEC_ARGUMENTS,
    ERROR_MEDIA_OUTPUT_UNWRITABLE,
    FFMPEG,
    FLAC_CODEC,
    FLAC_CODEC_ARGUMENTS,
    FLAC_EXTENSION,
    MATROSKA_EXTENSION,
    MATROSKA_MUXER,
    PARTIAL_SUFFIX,
    QUIET_ARGUMENTS,
    REPLACE_ATTEMPTS,
    REPLACE_RETRY_S,
)
from transcribe_ui.media.invoke import RunLimits, ToolInvoker, file_url, stderr_tail, tool_failed
from transcribe_ui.media.runner import ProcessResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Attempt:
    """One way of writing the track: container extension, muxer and codec arguments."""

    extension: AudioExtension
    muxer: str
    codec_arguments: tuple[str, ...]


def convert(
    invoker: ToolInvoker,
    source: Path,
    track_index: int,
    output_base: Path,
    limits: RunLimits,
) -> ConvertedAudio:
    """Write audio track `track_index` of `source` to `<output_base>.mka` (or `.flac`).

    `output_base` is the output path without its extension; its folder is created if missing.
    Each attempt has the full timeout of `limits`.

    Raises:
        DomainError: `media_tool_failed` if neither attempt succeeded, `media_output_unwritable`
            if the output folder or file cannot be written, or the errors of `ToolInvoker.run`.
    """
    try:
        output_base.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise _unwritable(error) from error
    selection = ("-i", file_url(source), "-map", f"0:a:{track_index}")
    copy = _Attempt(MATROSKA_EXTENSION, MATROSKA_MUXER, COPY_CODEC_ARGUMENTS)
    copied = _write(invoker, copy, selection, output_base, limits)
    if isinstance(copied, Path):
        return ConvertedAudio(path=copied, extension=copy.extension, fell_back=False)
    logger.info("Stream copy of audio track %s refused, encoding to FLAC: %s", track_index, stderr_tail(copied))
    flac = _Attempt(FLAC_EXTENSION, FLAC_CODEC, FLAC_CODEC_ARGUMENTS)
    encoded = _write(invoker, flac, selection, output_base, limits)
    if isinstance(encoded, Path):
        return ConvertedAudio(path=encoded, extension=flac.extension, fell_back=True)
    raise tool_failed(FFMPEG, "convert", encoded, CONVERT_FAILED_MESSAGE)


def output_path(output_base: Path, extension: AudioExtension) -> Path:
    """Return the finished output path of a conversion."""
    return output_base.with_name(f"{output_base.name}.{extension}")


def _write(
    invoker: ToolInvoker,
    attempt: _Attempt,
    selection: Sequence[str],
    output_base: Path,
    limits: RunLimits,
) -> Path | ProcessResult:
    """Return the finished file, or the failed process's result; the partial file never remains."""
    final = output_path(output_base, attempt.extension)
    partial = _new_partial(final)
    arguments = [*QUIET_ARGUMENTS, "-y", *selection, *attempt.codec_arguments]
    arguments += [*AUDIO_ONLY_ARGUMENTS, "-f", attempt.muxer, file_url(partial)]
    try:
        completed = invoker.run(FFMPEG, arguments, "convert", limits)
        if completed.returncode == 0:
            _move_into_place(partial, final)
            return final
    except BaseException:
        _remove(partial)
        raise
    _remove(partial)
    return completed


def _new_partial(final: Path) -> Path:
    """Create an empty partial file with a name of its own next to `final`.

    The name is unique per attempt, so an ffmpeg orphaned by an earlier run of the app can never
    write into a file that this attempt renames into place.
    """
    prefix = f"{final.name}."
    folder = final.parent
    try:
        descriptor, name = tempfile.mkstemp(suffix=PARTIAL_SUFFIX, prefix=prefix, dir=folder)
    except OSError as error:
        raise _unwritable(error) from error
    os.close(descriptor)
    return Path(name)


def _move_into_place(partial: Path, final: Path) -> None:
    """Rename the finished partial file; on Windows a scanner may hold a new file for a moment."""
    for _ in range(REPLACE_ATTEMPTS - 1):
        if _replaced(partial, final):
            return
        time.sleep(REPLACE_RETRY_S)
    try:
        partial.replace(final)
    except OSError as error:
        raise _unwritable(error) from error


def _replaced(partial: Path, final: Path) -> bool:
    """Rename once; False only for a Windows sharing violation, which is worth another try."""
    try:
        partial.replace(final)
    except PermissionError as error:
        if sys.platform != "win32":
            raise _unwritable(error) from error
        return False
    except OSError as error:
        raise _unwritable(error) from error
    return True


def _unwritable(error: OSError) -> DomainError:
    reason = error.strerror or type(error).__name__
    message = f"The converted audio cannot be written: {reason}"
    return DomainError(ERROR_MEDIA_OUTPUT_UNWRITABLE, message, {"operation": "convert", "reason": reason})


def _remove(path: Path) -> None:
    """Delete a partial output; a failure is logged, never raised over the error being handled."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete the partial output %s", path, exc_info=True)
