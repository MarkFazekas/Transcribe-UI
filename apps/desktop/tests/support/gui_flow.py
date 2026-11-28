"""The GUI's end-to-end flow compared with the CLI's (plan m6 M6.11).

`FlowRecorder` stands between the window's executor and the backend: it records every request
that reaches the backend (in the order the calls start), its answer and progress events, the ids
it drew from the seeded source and the clock when it started. `match_steps` finds the end-to-end steps among them
in order and refuses any other request that could change something. `replay` sends the matched
requests through the parity runner on a fresh leg built like the CLI flow's, recording the same
draws and clock per step, so the test compares both and the goldens.
"""

import bisect
import itertools
import json
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from support.end_to_end import STEPS, WORKER_CHANGES, change_legs, golden_bytes
from support.launch_legs import LaunchLegs
from support.normalise import PathNormaliser
from support.parity import Exchange, Template, run_parity
from support.upload import build_upload_tree
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import ProgressEvent, Request, Response
from transcribe_ui.api.ports import CancelToken, Clock, IdSource, Ports, ProgressSink
from transcribe_ui.cli.output import exit_code
from transcribe_ui.runs.responses import ReconcileCloudResponse, RefreshStatusResponse

# Status of an answer that asks for a confirmation (the request changed nothing).
ASKS = "needs_confirmation"
# Requests that only read: local state, settings, credentials, the cloud's setup, plans and previews.
READ_ONLY_ACTIONS = frozenset(
    (
        "get_app_info",
        "get_third_party_notices",
        "get_settings",
        "credentials_status",
        "check_credentials",
        "cloud_setup_status",
        "scan_tree",
        "preview_selection",
        "list_batches",
        "get_batch",
        "list_runs",
        "plan_launch",
        "plan_download",
    ),
)


@dataclass
class Sent:
    """One request that reached the backend: its answer, the ids it drew and the clock when it started."""

    index: int
    request: Request
    clock: datetime
    draws: list[str] = field(default_factory=list)
    events: list[ProgressEvent] = field(default_factory=list)
    response: Response | None = None

    def name(self) -> str:
        """Return how a failure names the request."""
        return f"request {self.index} ({self.request.action}: {self.request.model_dump_json()})"


@dataclass
class _Running:
    """Marks the request a pool thread runs while the block runs, so its draws are attributed to it."""

    mark: threading.local
    sent: Sent

    def __enter__(self) -> None:
        """Mark the request."""
        self.mark.sent = self.sent

    def __exit__(self, *_exc_info: object) -> None:
        """Clear the mark, however the block ended."""
        self.mark.sent = None


@dataclass
class _Forwarding:
    """A progress sink that keeps each event of a request and passes it on to the window's sink."""

    sent: Sent
    inner: ProgressSink | None

    def emit(self, event: ProgressEvent) -> None:
        """Keep the event, then pass it on."""
        self.sent.events.append(event)
        if self.inner is not None:
            self.inner.emit(event)


class FlowRecorder:
    """Runs the window's requests on a backend and records them; also the id source of that backend.

    Thread-safe: the window sends requests from its pool threads. A draw is attributed to the
    request running on the thread that drew it.
    """

    def __init__(self, clock: Clock) -> None:
        """Record the clock of `clock` per request; `attach` gives the backend."""
        self._clock = clock
        self._lock = threading.Lock()
        self._current = threading.local()
        self._backend: Backend | None = None
        self._ids: IdSource | None = None
        self.sent: list[Sent] = []
        # Draws on a thread that ran no recorded request (a test failure names them).
        self.stray_draws: list[str] = []

    def wrap(self, ports: Ports) -> Ports:
        """Return the ports with this recorder as their id source (drawing from theirs)."""
        self._ids = ports.ids
        return replace(ports, ids=self)

    def attach(self, backend: Backend) -> None:
        """Run the requests on `backend` (built over the wrapped ports)."""
        self._backend = backend

    def new_id(self) -> str:
        """Draw an id from the seeded source; record it with the request running on this thread."""
        assert self._ids is not None
        drawn = self._ids.new_id()
        current: Sent | None = getattr(self._current, "sent", None)
        with self._lock:
            if current is None:
                self.stray_draws.append(drawn)
            else:
                current.draws.append(drawn)
        return drawn

    def execute(self, request: Request, progress: ProgressSink | None, cancel: CancelToken | None) -> Response:
        """Record the request, run it, record its progress events and its answer."""
        assert self._backend is not None
        with self._lock:
            sent = Sent(len(self.sent), request, self._clock.now())
            self.sent.append(sent)
        with _Running(self._current, sent):
            response = self._backend.execute(request, _Forwarding(sent, progress), cancel)
        sent.response = response
        return response

    def answered(self) -> list[Sent]:
        """Return the recorded requests; each must have its answer by now."""
        with self._lock:
            found = list(self.sent)
        unanswered = [sent.name() for sent in found if sent.response is None]
        assert not unanswered, f"still running: {unanswered}"
        return found


def _status(sent: Sent) -> str:
    assert sent.response is not None
    return sent.response.status


def _golden_status(index: int) -> tuple[str, str]:
    """Return the action and the status of a step's golden answer."""
    answer = json.loads(golden_bytes(index))["response"]
    return answer["action"], answer["status"]


def _found_nothing(response: Response | None) -> bool:
    """Return whether the answer is a cloud check's that found and changed nothing.

    A refresh with no run, ready batch or held manifest; a reconcile with no run, no machine and
    nothing needing attention.
    """
    if isinstance(response, RefreshStatusResponse):
        reports = (response.runs, response.batches, response.busy, response.published, response.held)
        return not any(reports)
    if isinstance(response, ReconcileCloudResponse):
        return not (response.entries or response.runs or response.attention)
    return False


def _changes_nothing(sent: Sent) -> bool:
    """Return whether a request outside the steps changed nothing: it only reads, asked, or is a check that found nothing."""
    if sent.request.action in READ_ONLY_ACTIONS or _status(sent) == ASKS:
        return True
    return _found_nothing(sent.response)


def _is_step(sent: Sent, expected: tuple[str, str] | None) -> bool:
    """Return whether the request is the expected step's: its action, and it asks as the golden answer does."""
    if expected is None:
        return False
    action, status = expected
    return sent.request.action == action and (_status(sent) == ASKS) == (status == ASKS)


def match_steps(sent: Sequence[Sent], *, complete: bool = True) -> list[Sent]:
    """Return the requests of the end-to-end steps, in order; fail on any other request that may change something.

    A request is a step's when it is the next step's action and, like the step's golden answer,
    asks for a confirmation or does not (the GUI asks first where the CLI flow confirms at once).
    With `complete`, every step must have been sent.
    """
    expected = [_golden_status(index) for index in range(len(STEPS))]
    matched: list[Sent] = []
    for each in sent:
        following = expected[len(matched)] if len(matched) < len(expected) else None
        if _is_step(each, following):
            matched.append(each)
        else:
            assert _changes_nothing(each), f"{each.name()} is no step of the flow and may change something"
    sent_steps = len(matched)
    missing = STEPS[sent_steps:]
    assert not complete or not missing, f"the GUI never sent the steps {missing}"
    return matched


def gui_exchanges(matched: Sequence[Sent], root: Path) -> list[Exchange]:
    """Return the GUI's own outcomes of the steps so far (answer, events, exit code), normalised like the parity runner's."""
    normaliser = PathNormaliser([root])
    found: list[Exchange] = []
    for each in matched:
        assert each.response is not None
        events = tuple(normaliser.model_json(event) for event in each.events)
        found.append(Exchange(normaliser.response_json(each.response), events, exit_code(each.response)))
    return found


def request_document(request: Request, root: Path) -> str:
    """Return the request as a parity runner document: paths under `root` as `<root>/...`."""
    return request.model_dump_json(context=PathNormaliser([root]).context())


# An id the GUI drew, with the request that drew it.
type Drawn = tuple[Sent, str]


@dataclass
class Replayed:
    """What the replay of the steps gave: its exchanges, and per step the ids drawn and the clock."""

    exchanges: list[Exchange]
    draws: list[list[str]]
    clocks: list[datetime]


@dataclass
class _ReplayRecord:
    """Draws of the replay's backend leg and the boundaries of the steps in them."""

    legs: LaunchLegs
    draws: list[str] = field(default_factory=list)
    # Per step: how many ids were drawn before it, and the clock when it was sent.
    starts: list[tuple[int, datetime]] = field(default_factory=list)

    def ports(self, root: Path) -> Ports:
        """Return a leg's ports; the backend leg's (the first) records its draws."""
        ports = self.legs(root)
        if len(self.legs.clouds) > 1:
            return ports
        return replace(ports, ids=_Drawing(ports.ids, self.draws))

    def template(self, name: str, document: str) -> Template:
        """Return the step's template: the worker's change before it, then its document."""
        return lambda exchanges: self._before(name, document, exchanges)

    def step_draws(self) -> list[list[str]]:
        """Return per step the ids the backend leg drew while it ran."""
        bounds = [start for start, _clock in self.starts]
        bounds.append(len(self.draws))
        return [self.draws[first:last] for first, last in itertools.pairwise(bounds)]

    def clocks(self) -> list[datetime]:
        """Return per step the clock when it was sent."""
        return [clock for _start, clock in self.starts]

    def _before(self, name: str, document: str, exchanges: list[Exchange]) -> str:
        change = WORKER_CHANGES.get(name)
        if change is not None:
            change_legs(self.legs, change, exchanges)
        backend_leg = self.legs.clouds[0]
        self.starts.append((len(self.draws), backend_leg.clock.now()))
        return document


@dataclass
class _Drawing:
    """An id source that records what it draws."""

    inner: IdSource
    draws: list[str]

    def new_id(self) -> str:
        """Draw and record an id."""
        drawn = self.inner.new_id()
        self.draws.append(drawn)
        return drawn


def replay(tmp_path: Path, documents: Sequence[str]) -> Replayed:
    """Replay the steps' documents through the parity runner on fresh legs built like the CLI flow's.

    The moto mock must be active. The simulated worker changes the legs before the same steps.
    """
    record = _ReplayRecord(LaunchLegs())
    templates = [record.template(name, document) for name, document in zip(STEPS, documents, strict=True)]
    exchanges = run_parity(tmp_path, templates, prepare=build_upload_tree, ports=record.ports)
    return Replayed(exchanges, record.step_draws(), record.clocks())


def _draws_by_step(matched: Sequence[Sent], sent: Sequence[Sent]) -> list[list[Drawn]]:
    """Return per step the ids the GUI drew since the step before (each with its request); none may come later."""
    ends = [each.index for each in matched]
    by_step: list[list[Drawn]] = [[] for _step in matched]
    for each in sent:
        step = bisect.bisect_left(ends, each.index)
        assert step < len(ends) or not each.draws, f"{each.name()} drew {each.draws} after the last step"
        if each.draws:
            by_step[step].extend((each, drawn) for drawn in each.draws)
    return by_step


def assert_same_draws(matched: Sequence[Sent], sent: Sequence[Sent], replayed: Replayed) -> None:
    """Assert the GUI drew the replay's ids per step: those of the requests since the step before, then the step's.

    A failure names the first GUI request whose draws differ from the replay's.
    """
    by_step = _draws_by_step(matched, sent)
    for step, (drawn, expected) in enumerate(zip(by_step, replayed.draws, strict=True)):
        _assert_step_draws(STEPS[step], matched[step], drawn, expected)


def _assert_step_draws(name: str, step: Sent, drawn: list[Drawn], expected: list[str]) -> None:
    """Assert the GUI's draws for one step are the replay's, in order."""
    for position, (each, found) in enumerate(drawn):
        wanted = expected[position] if position < len(expected) else None
        assert found == wanted, f"{each.name()} drew {found}; the replay of {name} drew {wanted}"
    drawn_count = len(drawn)
    unmatched = expected[drawn_count:]
    assert not unmatched, f"{step.name()} drew too few ids: the replay of {name} also drew {unmatched}"


def assert_same_clock(matched: Sequence[Sent], replayed: Replayed) -> None:
    """Assert each step was sent at the replay's clock (the worker's changes came at the same points)."""
    for step, (each, clock) in enumerate(zip(matched, replayed.clocks, strict=True)):
        assert each.clock == clock, f"{each.name()} was sent at {each.clock}; the replay of {STEPS[step]} at {clock}"
