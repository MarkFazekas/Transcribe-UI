"""Running one tool call and turning its failures into domain errors."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from transcribe_ui.api.errors import Details, DomainError
from transcribe_ui.api.ports import CancelToken
from transcribe_ui.media.classes import Operation, Tool
from transcribe_ui.media.constants import (
    ERROR_MEDIA_INDEX_INVALID,
    ERROR_MEDIA_TOOL_FAILED,
    ERROR_MEDIA_TOOL_NOT_FOUND,
    ERROR_MEDIA_TOOL_TIMEOUT,
    FILE_PROTOCOL,
    STDERR_TAIL_CHARS,
)
from transcribe_ui.media.runner import ProcessResult, ProcessRunner, ProcessTimeoutError
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
