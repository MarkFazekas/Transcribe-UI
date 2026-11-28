"""The retry and cleanup handlers over a seeded cloud world, with a recording outlet (plan m6 M6.10).

The world is one parity leg of the retry and cleanup tests (its own moto bucket and fake Runpod
API, the cloud set up): the transcribed source batch (the film failed, the podcast found no
speech) and another computer's manifest, so the requests the handlers build replay alike
through the parity runner.
"""

from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from support.batches import build_batch_tree
from support.cleanup import seed_transcribed
from support.cloud import CloudWorld, ManualExecutor, RecordingRunner
from support.credentials import moto_bucket
from support.jobs import JobsArea, jobs_area
from support.launch import write_foreign
from support.launch_legs import LaunchLegs
from support.runpod import Cloud
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.ports import Ports
from transcribe_ui.gui.activity import Activity
from transcribe_ui.gui.cleanup.area import CleanupHandlers, build_handlers
from transcribe_ui.gui.cleanup.display import CleanupQuestion
from transcribe_ui.gui.executor import SyncExecutor
from transcribe_ui.gui.feedback import ErrorView
from transcribe_ui.storage.r2 import R2ObjectStore

# A signal the handlers emitted: its name and its arguments.
type Emitted = tuple[str, tuple[object, ...]]

# The bus signal that reloads the jobs pages.
CHANGED_SIGNAL = "batches_changed"


def seed_cleanup_leg(ports: Ports, root: Path, store: R2ObjectStore, _cloud: Cloud) -> None:
    """Seed a leg with the transcribed source batch (film failed, podcast no speech) and another computer's job."""
    seed_transcribed(ports.state, root, store, podcast="no_speech")
    write_foreign(store)


@contextmanager
def cleanup_world(tmp_path: Path) -> Iterator[CloudWorld]:
    """Return one seeded leg (closed afterwards)."""
    root = tmp_path / "gui"
    root.mkdir()
    build_batch_tree(root)
    legs = LaunchLegs(seed=seed_cleanup_leg)
    with moto_bucket():
        ports = legs(root)
        with closing(ports.state):
            yield CloudWorld(ports, legs.clouds[0], legs.stores[0], root)


@dataclass
class RecordingOutlet:
    """Records what the handlers send to the window; accepts each question at once when `accept`."""

    accept: bool = True
    emitted: list[Emitted] = field(default_factory=list)
    asked: list[CleanupQuestion] = field(default_factory=list)
    errors: list[ErrorView] = field(default_factory=list)
    # The re-send each error offers (its `app_busy` fix), in the same order.
    retries: list[Callable[[], object] | None] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def emit(self, signal_name: str, *arguments: object) -> None:
        """Record a signal."""
        self.emitted.append((signal_name, arguments))

    def ask(self, question: CleanupQuestion, on_confirm: Callable[[], None]) -> None:
        """Record the question; confirm at once when `accept`."""
        self.asked.append(question)
        if self.accept:
            on_confirm()

    def show_error(self, view: ErrorView, retry: Callable[[], object] | None) -> None:
        """Record the error and its re-send."""
        self.errors.append(view)
        self.retries.append(retry)

    def notify(self, text: str) -> None:
        """Record the note."""
        self.notes.append(text)

    def signals(self, signal_name: str) -> list[tuple[object, ...]]:
        """Return the arguments of each emission of a signal."""
        return [arguments for name, arguments in self.emitted if name == signal_name]


@dataclass
class CleanupArea:
    """The handlers over a backend, with their activity panel, outlet and recorded requests."""

    handlers: CleanupHandlers
    activity: Activity
    outlet: RecordingOutlet
    runner: RecordingRunner


def cleanup_area(backend: Backend, *, manual: bool = False) -> tuple[CleanupArea, ManualExecutor | None]:
    """Return the handlers on `SyncExecutor` (or, with `manual`, on a `ManualExecutor` the test drives)."""
    runner = RecordingRunner(backend)
    outlet = RecordingOutlet()
    executor = ManualExecutor(runner) if manual else SyncExecutor(runner)
    activity = Activity(executor)
    area = CleanupArea(build_handlers(activity, outlet), activity, outlet, runner)
    return area, executor if isinstance(executor, ManualExecutor) else None


def only[ItemT](found: Sequence[ItemT]) -> ItemT:
    """Return the one element of `found` (fails unless there is exactly one)."""
    assert len(found) == 1, found
    return found[0]


def retry_id_of(area: CleanupArea, source: str) -> str:
    """Return the retry job named by the first reload, which names the source and then the retry job."""
    names = only(area.outlet.signals(CHANGED_SIGNAL)[:1])[0]
    assert isinstance(names, tuple)
    named_source, retry_id = names
    assert (named_source, isinstance(retry_id, str)) == (source, True)
    return str(retry_id)


@dataclass
class JoinedPages:
    """The jobs pages and the handlers, joined like the window's bus joins them."""

    jobs: JobsArea
    area: CleanupArea
    # The handlers' signals already handed to the job page.
    seen: int = 0

    def forward(self) -> None:
        """Hand the job page's action signals to the handlers, and the handlers' changes to the job page."""
        handlers = self.area.handlers
        for name, arguments in self.jobs.outlet.emitted:
            if name == "retry_jobs":
                batch_id, jobs = arguments
                handlers.retry.retry(str(batch_id), jobs)
            elif name == "clean_up":
                batch_id, scope = arguments
                handlers.cleanup.clean_up(str(batch_id), str(scope))
        self.jobs.outlet.emitted.clear()
        start = self.seen
        for name, arguments in self.area.outlet.emitted[start:]:
            if name == CHANGED_SIGNAL:
                self.jobs.page.on_batches_changed(arguments[0])
        self.seen = len(self.area.outlet.emitted)


def joined_pages(backend: Backend, area: CleanupArea, batch_id: str) -> JoinedPages:
    """Return the job page of `batch_id` joined to the handlers."""
    pages = JoinedPages(jobs_area(backend), area)
    pages.jobs.page.open_batch(batch_id)
    return pages
