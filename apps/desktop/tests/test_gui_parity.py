"""The `gui` leg of the parity runner: requests through `QtExecutor` equal the backend (plan m6 M6.2)."""

import json
from contextlib import closing
from functools import partial
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from support.discovery import build_media_tree, scan_request
from support.gated import DRAIN_MS, UPLOAD, wait_for
from support.golden import assert_golden
from support.gui_parity import run_gui_one, run_gui_parity, via_backend
from support.parity import Exchange, Leg, RecordingProgress, RoutesFactory
from transcribe_ui.api.classes import (
    Confirmation,
    ErrorResponse,
    GetAppInfoRequest,
    NeedsConfirmation,
    ProgressEvent,
    Response,
)
from transcribe_ui.api.errors import CancelledError, DomainError
from transcribe_ui.api.info import get_app_info
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.api.routes import Route, Routes
from transcribe_ui.api.schema import parse_request
from transcribe_ui.cli.output import exit_code
from transcribe_ui.gui.bridge import QtExecutor
from transcribe_ui.upload.classes import RunUploadRequest

APP_INFO_JSON = '{"action": "get_app_info"}'
UPLOAD_JSON = '{"action": "run_upload", "batch_id": "batch_20261127_120000"}'
# Progress events of the chatty stub.
MANY_EVENTS = 500
CRASH_DETAIL = "internal detail that must not leak"
# Documents of the mixed parallel run (each one independent of the others).
MIXED = (
    scan_request(),
    '{"action": "get_settings"}',
    '{"action": "credentials_status"}',
    '{"action": "list_batches"}',
    '{"action": "get_app_info"}',
    '{"action": "refresh_status"}',
    '{"action": "get_batch", "batch_id": "batch_20261127_120000"}',
)


def _upload_then_cancelled(request: RunUploadRequest, context: RequestContext) -> Response:
    for done in range(2):
        context.progress.emit(ProgressEvent(action=request.action, stage="upload", completed=done, total=2))
    raise CancelledError


def _many_events(request: GetAppInfoRequest, context: RequestContext) -> Response:
    for done in range(MANY_EVENTS):
        context.progress.emit(ProgressEvent(action=request.action, stage="count", completed=done, total=MANY_EVENTS))
    return get_app_info(request, context)


def _chatty(_root: Path) -> Routes:
    return {"get_app_info": Route(run=_many_events, needs_database=False)}


def _ask_confirmation(root: Path, request: GetAppInfoRequest, context: RequestContext) -> Response:
    path = str(root / "media" / "clip.mkv")
    context.progress.emit(ProgressEvent(action=request.action, stage="probe", completed=1, total=2, path=path))
    confirmation = Confirmation(field="confirmed", message="Overwrite the existing subtitle?", details={"count": 1})
    return NeedsConfirmation(action=request.action, confirmations=(confirmation,))


def _refuse(_request: GetAppInfoRequest, _context: RequestContext) -> Response:
    code = "batch_busy"
    message = "Another request is working on this batch."
    raise DomainError(code, message, {"batch_id": "batch_1"})


def _crash(_request: GetAppInfoRequest, _context: RequestContext) -> Response:
    raise RuntimeError(CRASH_DETAIL)


def _cancelling(_root: Path) -> Routes:
    return {UPLOAD: Route(run=_upload_then_cancelled, needs_database=False)}


def _confirming(root: Path) -> Routes:
    return {"get_app_info": Route(run=partial(_ask_confirmation, root), needs_database=False)}


def _refusing(_root: Path) -> Routes:
    return {"get_app_info": Route(run=_refuse, needs_database=False)}


def _crashing(_root: Path) -> Routes:
    return {"get_app_info": Route(run=_crash, needs_database=False)}


# The responses one call delivered (exactly one once it completed).
type Answers = list[Response]
# What one submit of the mixed run collects.
type Collected = tuple[Answers, RecordingProgress]


def _only(responses: Answers) -> Response:
    assert len(responses) == 1
    return responses[0]


def test_app_info_is_the_same_through_the_gui(tmp_path: Path, qtbot: QtBot) -> None:
    """`get_app_info` through `QtExecutor` equals the backend leg and the shared golden."""
    exchange = run_gui_one(tmp_path, qtbot, APP_INFO_JSON)
    assert_golden("get_app_info.json", exchange.golden())


def test_scan_is_the_same_through_the_gui(tmp_path: Path, qtbot: QtBot) -> None:
    """A scan through the GUI equals the backend leg and the scan golden."""
    exchange = run_gui_one(tmp_path, qtbot, scan_request(), prepare=build_media_tree)
    assert_golden("scan_tree.json", exchange.golden())


def test_progress_arrives_in_order(tmp_path: Path, qtbot: QtBot) -> None:
    """Many events from a pool thread arrive on the GUI thread one by one, in order, before the response."""
    exchange = run_gui_one(tmp_path, qtbot, APP_INFO_JSON, _chatty)
    counts = [json.loads(event)["completed"] for event in exchange.events]
    assert counts == list(range(MANY_EVENTS))


@pytest.mark.parametrize(
    ("template", "routes"),
    [
        (UPLOAD_JSON, _cancelling),
        (APP_INFO_JSON, _confirming),
        (APP_INFO_JSON, _refusing),
        (APP_INFO_JSON, _crashing),
    ],
    ids=["cancelled_long", "needs_confirmation", "domain_error", "internal_error"],
)
def test_stub_outcomes_pass_unchanged(
    tmp_path: Path,
    qtbot: QtBot,
    template: str,
    routes: RoutesFactory,
) -> None:
    """Cancelled long requests, confirmations and errors pass the bridge unchanged."""
    exchanges = run_gui_parity(tmp_path, qtbot, [template], routes)
    assert CRASH_DETAIL not in exchanges[0].response


def test_mixed_requests_in_parallel(tmp_path: Path, qtbot: QtBot) -> None:
    """Scans, settings, lists and a refresh at once on the seeded backend answer as when sent one by one."""
    direct = Leg.create(tmp_path / "backend", None, build_media_tree)
    parallel = Leg.create(tmp_path / "gui", None, build_media_tree)
    executor = QtExecutor(parallel.backend)
    with closing(direct), closing(parallel):
        expected = [via_backend(direct, template) for template in MIXED]
        outcomes = [_submit(executor, parallel, template) for template in MIXED]
        wait_for(qtbot, lambda: executor.pending() == 0)
        actual = [_exchange(parallel, outcome) for outcome in outcomes]
        assert executor.wait_for_threads(DRAIN_MS)
    assert actual == expected


def _submit(executor: QtExecutor, leg: Leg, template: str) -> Collected:
    request = parse_request(leg.document(template))
    assert not isinstance(request, ErrorResponse)
    responses: Answers = []
    progress = RecordingProgress()
    executor.submit(request, responses.append, progress.emit)
    return responses, progress


def _exchange(leg: Leg, outcome: Collected) -> Exchange:
    responses, progress = outcome
    response = _only(responses)
    return leg.exchange(response, progress.events, exit_code(response))
