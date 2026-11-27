"""The `gui` leg of the parity runner: the same requests through `QtExecutor` (plan m6 §1 "Testing").

Each request document runs on a backend leg (`Backend.execute_json`, as in `support.parity`)
and on a GUI leg, where it is validated into a `Request` and sent through `QtExecutor` on its
pool; the response, the progress events and their order must equal the backend leg's. Legs are
built by `support.parity.Leg.create`, unchanged.
"""

from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

from pytestqt.qtbot import QtBot

from support.gated import DRAIN_MS
from support.parity import Exchange, Leg, Prepare, RecordingProgress, RoutesFactory, Template
from transcribe_ui.api.classes import ErrorResponse, Response
from transcribe_ui.api.schema import parse_request
from transcribe_ui.cli.output import exit_code
from transcribe_ui.gui.bridge import QtExecutor

# How long a GUI leg waits for one response, in milliseconds.
RESPONSE_TIMEOUT_MS = 30_000


def via_backend(leg: Leg, template: str) -> Exchange:
    """Run one request document directly on the leg's backend."""
    progress = RecordingProgress()
    response = leg.backend.execute_json(leg.document(template), progress)
    return leg.exchange(response, progress.events, exit_code(response))


def via_gui(leg: Leg, executor: QtExecutor, qtbot: QtBot, template: str) -> Exchange:
    """Run one request document through the GUI's executor, waiting on the event loop for its response."""
    request = parse_request(leg.document(template))
    if isinstance(request, ErrorResponse):
        # The GUI builds request models, so it never sends an invalid document; nothing to run.
        return leg.exchange(request, [], exit_code(request))
    progress = RecordingProgress()
    responses: list[Response] = []
    executor.submit(request, responses.append, progress.emit)
    qtbot.waitUntil(lambda: bool(responses), timeout=RESPONSE_TIMEOUT_MS)
    assert len(responses) == 1
    response = responses[0]
    return leg.exchange(response, progress.events, exit_code(response))


def run_gui_parity(
    tmp_path: Path,
    qtbot: QtBot,
    templates: Sequence[Template],
    routes: RoutesFactory | None = None,
    prepare: Prepare | None = None,
) -> list[Exchange]:
    """Run the documents through the backend and the GUI legs, assert they agree, return the GUI's outcomes."""
    direct = Leg.create(tmp_path / "backend", routes, prepare)
    via_qt = Leg.create(tmp_path / "gui", routes, prepare)
    executor = QtExecutor(via_qt.backend)
    exchanges: list[Exchange] = []
    with closing(direct), closing(via_qt):
        for index, step in enumerate(templates):
            template = step if isinstance(step, str) else step(exchanges)
            expected = via_backend(direct, template)
            actual = via_gui(via_qt, executor, qtbot, template)
            assert actual == expected, f"request {index}: the GUI and the backend disagree"
            exchanges.append(actual)
        executor.shutdown()
        assert executor.wait_for_threads(DRAIN_MS)
    return exchanges


def run_gui_one(
    tmp_path: Path,
    qtbot: QtBot,
    template: str,
    routes: RoutesFactory | None = None,
    prepare: Prepare | None = None,
) -> Exchange:
    """Run one request document through both legs; return the GUI leg's outcome."""
    return run_gui_parity(tmp_path, qtbot, [template], routes, prepare)[0]
