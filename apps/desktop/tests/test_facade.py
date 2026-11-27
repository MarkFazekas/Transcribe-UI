"""Flow tests of the backend façade (architecture §3.1, §7; plan m5 §1).

Every request runs through `Backend.execute` and through the CLI's `main`; both must give the
same normalised response, progress events and exit code. Golden files and schema snapshots are
regenerated with `UPDATE_SNAPSHOTS=1 uv run pytest apps/desktop/tests`.
"""

import json
import os
from functools import partial
from pathlib import Path
from typing import Literal

import pytest

from support.golden import SNAPSHOT_DIR, assert_golden, assert_matches_file
from support.normalise import PathNormaliser
from support.parity import RoutesFactory, run_one
from transcribe_ui.api.backend import Route, Routes, default_routes
from transcribe_ui.api.classes import Confirmation, GetAppInfoRequest, NeedsConfirmation, ProgressEvent, Response
from transcribe_ui.api.errors import DomainError
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.api.schema import render_schema
from transcribe_ui.cli.constants import EXIT_ERROR, EXIT_INVALID_REQUEST, EXIT_NEEDS_CONFIRMATION, EXIT_OK

GET_APP_INFO = '{"action": "get_app_info"}'
CRASH_DETAIL = "internal detail that must not leak"
MEDIA_PATH_PARTS = ("media", "Movies", "clip.mkv")
NORMALISED_MEDIA_PATH = "<root>/media/Movies/clip.mkv"


def _ask_confirmation(root: Path, request: GetAppInfoRequest, context: RequestContext) -> Response:
    path = str(root.joinpath(*MEDIA_PATH_PARTS))
    context.progress.emit(ProgressEvent(action=request.action, stage="probe", completed=1, total=2, path=path))
    confirmation = Confirmation(field="confirmed", message="Overwrite the existing subtitle?", details={"count": 1})
    return NeedsConfirmation(action=request.action, confirmations=(confirmation,))


def _refuse(_request: GetAppInfoRequest, _context: RequestContext) -> Response:
    code = "batch_busy"
    message = "Another request is working on this batch."
    raise DomainError(code, message, {"batch_id": "batch_20261127_120000"})


def _crash(_request: GetAppInfoRequest, _context: RequestContext) -> Response:
    raise RuntimeError(CRASH_DETAIL)


def _confirming(root: Path) -> Routes:
    return {"get_app_info": Route(run=partial(_ask_confirmation, root), needs_database=False)}


def _refusing(_root: Path) -> Routes:
    return {"get_app_info": Route(run=_refuse, needs_database=False)}


def _crashing(_root: Path) -> Routes:
    return {"get_app_info": Route(run=_crash, needs_database=False)}


def test_get_app_info_matches_on_both_legs(tmp_path: Path) -> None:
    """`get_app_info` gives the same versions through the backend and the CLI."""
    exchange = run_one(tmp_path, GET_APP_INFO)
    assert exchange.exit_code == EXIT_OK
    assert_golden("get_app_info.json", exchange.golden())


@pytest.mark.parametrize(
    ("name", "document"),
    [
        ("unknown_action", '{"action": "no_such_action"}'),
        ("extra_field", '{"action": "get_app_info", "verbose": true}'),
        ("not_json", "{not json"),
    ],
)
def test_invalid_request_is_refused_on_both_legs(tmp_path: Path, name: str, document: str) -> None:
    """An invalid request document gives the same error JSON and exit code 2 on both legs."""
    exchange = run_one(tmp_path, document)
    assert exchange.exit_code == EXIT_INVALID_REQUEST
    assert_golden(f"invalid_request_{name}.json", exchange.golden())


@pytest.mark.parametrize(
    ("name", "routes", "expected_exit"),
    [
        ("needs_confirmation", _confirming, EXIT_NEEDS_CONFIRMATION),
        ("domain_error", _refusing, EXIT_ERROR),
        ("internal_error", _crashing, EXIT_ERROR),
    ],
)
def test_stub_outcomes_match_on_both_legs(
    tmp_path: Path,
    name: str,
    routes: RoutesFactory,
    expected_exit: int,
) -> None:
    """Confirmations, domain errors and crashes (internals never leaked) map the same everywhere."""
    exchange = run_one(tmp_path, GET_APP_INFO, routes)
    assert exchange.exit_code == expected_exit
    assert CRASH_DETAIL not in exchange.response
    assert_golden(f"stub_{name}.json", exchange.golden())


def test_normaliser_accepts_raw_and_resolved_root(tmp_path: Path) -> None:
    """A path under the root as created or as resolved normalises to `<root>/...` with `/`."""
    real_root = tmp_path / "real"
    real_root.mkdir()
    root = tmp_path / "link"
    try:
        root.symlink_to(real_root, target_is_directory=True)
    except OSError:
        root = real_root  # No symlink privilege (Windows); the 8.3 short temp path differs there anyway.
    normaliser = PathNormaliser([root])
    resolved_root = Path(os.path.realpath(root))
    sibling_root = resolved_root.with_name(f"{resolved_root.name}-other")
    assert normaliser.path(str(root.joinpath(*MEDIA_PATH_PARTS))) == NORMALISED_MEDIA_PATH
    assert normaliser.path(str(resolved_root.joinpath(*MEDIA_PATH_PARTS))) == NORMALISED_MEDIA_PATH
    assert normaliser.path(str(sibling_root.joinpath(*MEDIA_PATH_PARTS))).endswith("-other/media/Movies/clip.mkv")


@pytest.mark.parametrize(("kind", "snapshot"), [("request", "Request"), ("response", "Response")])
def test_schema_matches_snapshot(kind: Literal["request", "response"], snapshot: str) -> None:
    """The JSON Schema of each union equals the committed snapshot; any change is a visible diff."""
    assert_matches_file(SNAPSHOT_DIR / f"{snapshot}.schema.json", render_schema(kind))


def test_every_request_has_a_route() -> None:
    """The production dispatch table covers exactly the actions of the request union."""
    schema = json.loads(render_schema("request"))
    assert set(schema["discriminator"]["mapping"]) == set(default_routes())
