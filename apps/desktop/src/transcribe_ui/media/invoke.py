"""Running one tool call and turning its failures into domain errors.

`bundled_tool_problems` also lives here (not in `tools.py`, which this module imports): it runs
the tools of a release build's bundle through the same locator and invoker as every tool call.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from transcribe_ui.api.errors import Details, DomainError
from transcribe_ui.api.ports import CancelFlag, CancelToken
from transcribe_ui.media.classes import Operation, Tool
from transcribe_ui.media.constants import (
    ERROR_MEDIA_INDEX_INVALID,
    ERROR_MEDIA_TOOL_FAILED,
    ERROR_MEDIA_TOOL_NOT_FOUND,
    ERROR_MEDIA_TOOL_TIMEOUT,
    FFMPEG,
    FFPROBE,
    FILE_PROTOCOL,
    STDERR_TAIL_CHARS,
    VERSION_OPTION,
    VERSION_TIMEOUT_S,
)
from transcribe_ui.media.runner import ProcessResult, ProcessRunner, ProcessTimeoutError, SubprocessRunner
from transcribe_ui.media.tools import ToolLocator


@dataclass(frozen=True)
class RunLimits:
    """Limits of one tool call: its timeout and the cancel token that stops it."""

    timeout_s: float
    cancel: CancelToken


@dataclass(frozen=True)
class ToolInvoker:
    """Resolves a tool and runs it through the process runner."""

    locator: ToolLocator
    runner: ProcessRunner

    def run(self, tool: Tool, arguments: Sequence[str], operation: Operation, limits: RunLimits) -> ProcessResult:
        """Run the tool with the arguments; a non-zero exit is returned, not raised.

        Raises:
            DomainError: `media_tool_not_found` (missing, or it cannot be started),
                `media_tool_timeout` (killed after the timeout), `cancelled` (killed on cancel).
        """
        argv = [str(self.locator.resolve(tool)), *arguments]
        try:
            return self.runner.run(argv, limits.timeout_s, limits.cancel)
        except ProcessTimeoutError as error:
            message = f"{tool} did not finish ({operation}) within {limits.timeout_s:g} s and was stopped."
            details = {"tool": tool, "operation": operation, "timeout_s": limits.timeout_s}
            raise DomainError(ERROR_MEDIA_TOOL_TIMEOUT, message, details) from error
        except OSError as error:
            reason = error.strerror or type(error).__name__
            message = f"{tool} could not be started: {reason}"
            started: Details = {"tool": tool, "source": "start", "reason": reason}
            raise DomainError(ERROR_MEDIA_TOOL_NOT_FOUND, message, started) from error


def require_index(index: int, operation: Operation) -> None:
    """Refuse a negative track or stream index before anything runs.

    Raises:
        DomainError: `media_index_invalid` if the index is negative.
    """
    if index < 0:
        message = f"The track or stream index must not be negative, got {index}."
        raise DomainError(ERROR_MEDIA_INDEX_INVALID, message, {"operation": operation, "index": index})


def tool_failed(tool: Tool, operation: Operation, completed: ProcessResult, summary: str) -> DomainError:
    """Return the error of a tool call that exited non-zero; only the end of stderr is kept."""
    stderr = stderr_tail(completed)
    details = {"tool": tool, "operation": operation, "exit_code": completed.returncode, "stderr": stderr}
    return DomainError(ERROR_MEDIA_TOOL_FAILED, summary, details)


def stderr_tail(completed: ProcessResult) -> str:
    """Return the end of the process's stderr as text (ffmpeg puts the reason last)."""
    text = completed.stderr.decode("utf-8", errors="replace").strip()
    return text[-STDERR_TAIL_CHARS:]


def file_url(path: Path) -> str:
    """Return the path as an ffmpeg `file:` URL, absolute, so no name is read as an option or protocol."""
    return f"{FILE_PROTOCOL}{path.absolute()}"


def _nowhere(_command: str) -> None:
    """Find nothing on `PATH`: a bundle check must not fall back on another copy."""


def bundled_tool_problems(folder: Path, runner: ProcessRunner | None = None) -> list[str]:
    """Return why the ffmpeg and ffprobe bundled in `folder` cannot be used (empty: both answer `-version`).

    Each is resolved by the app's own `ToolLocator` with `folder` as its bundle, without settings
    overrides or a `PATH` fallback, and run through `ToolInvoker` (the real `SubprocessRunner`
    unless `runner` is given) with `-version` under a short timeout. A tool that is missing or
    cannot be started, exits non-zero, or resolves outside `folder` is a problem.
    """
    locator = ToolLocator(bundle_dir=folder, which=_nowhere)
    invoker = ToolInvoker(locator, runner or SubprocessRunner())
    limits = RunLimits(VERSION_TIMEOUT_S, CancelFlag())
    problems: list[str] = []
    for tool in (FFMPEG, FFPROBE):
        problems.extend(_tool_problems(invoker, tool, folder, limits))
    return problems


def _tool_problems(invoker: ToolInvoker, tool: Tool, folder: Path, limits: RunLimits) -> list[str]:
    """Return why one bundled tool cannot be used (empty: it answered `-version`)."""
    try:
        resolved = invoker.locator.resolve(tool)
        completed = invoker.run(tool, [VERSION_OPTION], "version", limits)
    except DomainError as error:
        return [f"{tool}: {error.message}"]
    problems = []
    if not resolved.resolve().is_relative_to(folder.resolve()):
        problems.append(f"{tool} resolved to {resolved}, outside the bundle {folder}")
    if completed.returncode:
        exited = f"exited {completed.returncode}: {stderr_tail(completed)}"
        problems.append(f"{tool} {VERSION_OPTION} {exited}")
    return problems
