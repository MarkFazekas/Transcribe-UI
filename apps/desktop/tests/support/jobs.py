"""The jobs pages' view-models over the seeded scenarios, with a recording outlet and runner (plan m6 M6.5)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from support.actions import seed_scenarios, seeded_root
from support.fakes import seeded_ports
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import Request, Response
from transcribe_ui.api.ports import CancelToken, Ports, ProgressSink
from transcribe_ui.gui.executor import SyncExecutor
from transcribe_ui.gui.feedback import ConfirmationView, ErrorView
from transcribe_ui.gui.jobs.commands import JobCommands
from transcribe_ui.gui.jobs.viewmodel import JobPageModel, JobsListModel

# A signal the jobs pages emitted: its name and its arguments.
type Emitted = tuple[str, tuple[object, ...]]


def scenario_ports(root: Path) -> Ports:
    """Return seeded ports whose database holds every scenario's batch (close `state` when done)."""
    ports = seeded_ports(root)
    seed_scenarios(ports.state, seeded_root(root))
    return ports


@dataclass
class RecordingOutlet:
    """Records the signals, confirmations and errors the jobs pages send to the window."""

    # Whether a confirmation is accepted (Confirm) or declined (Cancel).
    accept: bool = True
    emitted: list[Emitted] = field(default_factory=list)
    asked: list[ConfirmationView] = field(default_factory=list)
    errors: list[ErrorView] = field(default_factory=list)

    def emit(self, signal_name: str, *arguments: object) -> None:
        """Record a signal."""
        self.emitted.append((signal_name, arguments))

    def confirm(self, view: ConfirmationView, on_confirm: Callable[[], None]) -> None:
        """Record the question; confirm at once when `accept`."""
        self.asked.append(view)
        if self.accept:
            on_confirm()

    def show_error(self, view: ErrorView, _retry: object) -> None:
        """Record the error."""
        self.errors.append(view)


@dataclass
class RecordingRunner:
    """Runs requests on a backend and records them."""

    backend: Backend
    requests: list[Request] = field(default_factory=list)

    def execute(self, request: Request, progress: ProgressSink | None, cancel: CancelToken | None) -> Response:
        """Record the request, then run it."""
        self.requests.append(request)
        return self.backend.execute(request, progress, cancel)


@dataclass
class JobsArea:
    """The jobs pages' view-models on `SyncExecutor`, sharing one outlet and one runner."""

    runner: RecordingRunner
    outlet: RecordingOutlet
    jobs_list: JobsListModel
    page: JobPageModel


def jobs_area(backend: Backend) -> JobsArea:
    """Return the list and page view-models over `backend`."""
    runner = RecordingRunner(backend)
    outlet = RecordingOutlet()
    executor = SyncExecutor(runner)
    commands = JobCommands(executor, outlet)
    jobs_list = JobsListModel(executor, commands, outlet)
    page = JobPageModel(executor, commands, outlet)
    return JobsArea(runner, outlet, jobs_list, page)
