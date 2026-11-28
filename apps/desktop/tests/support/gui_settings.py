"""Helpers of the settings area's tests: a recording executor, scripted answers and request goldens."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from support.normalise import PathNormaliser
from transcribe_ui.api.classes import Request, Response
from transcribe_ui.api.ports import CancelToken, ProgressSink
from transcribe_ui.gui.executor import (
    CallHandle,
    DoneCallback,
    ProgressCallback,
    RequestRunner,
    StartedCallback,
    SyncExecutor,
)
from transcribe_ui.gui.settings.hooks import AreaHooks

if TYPE_CHECKING:
    from transcribe_ui.gui.feedback import ErrorView


@dataclass
class Recorded:
    """An executor that runs requests inline (`SyncExecutor`) and keeps every request sent."""

    runner: RequestRunner
    sent: list[Request] = field(default_factory=list)

    def submit(
        self,
        request: Request,
        on_done: DoneCallback,
        on_progress: ProgressCallback | None = None,
        on_started: StartedCallback | None = None,
    ) -> CallHandle:
        """Keep the request, then run it inline."""
        self.sent.append(request)
        return SyncExecutor(self.runner).submit(request, on_done, on_progress, on_started)

    def actions(self) -> list[str]:
        """Return the actions sent, in order."""
        return [request.action for request in self.sent]


@dataclass(frozen=True)
class Scripted:
    """A runner that answers the listed actions itself and passes every other request on."""

    runner: RequestRunner
    answers: Mapping[str, Response]

    def execute(self, request: Request, progress: ProgressSink | None, cancel: CancelToken | None) -> Response:
        """Answer from the script, else from the wrapped runner."""
        answer = self.answers.get(request.action)
        if answer is not None:
            return answer
        return self.runner.execute(request, progress, cancel)


def request_golden(request: Request, root: Path) -> str:
    """Return a request as the text of a golden file: indented JSON, paths under `root` as `<root>`."""
    context = PathNormaliser([root]).context()
    return f"{request.model_dump_json(indent=2, context=context)}\n"


class Heard:
    """What the settings area's view-models told the window."""

    def __init__(self) -> None:
        """Start with nothing heard."""
        self.errors: list[ErrorView] = []
        self.saves = 0
        self.language_changes = 0

    def hooks(self) -> AreaHooks:
        """Return hooks that record what they hear."""
        return AreaHooks(self.errors.append, self._saved, self._language_changed)

    def _saved(self) -> None:
        self.saves += 1

    def _language_changed(self) -> None:
        self.language_changes += 1
