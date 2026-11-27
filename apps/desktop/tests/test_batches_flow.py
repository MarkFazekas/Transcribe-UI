"""Flow tests of the batch requests through the CLI and the backend (plan m5 M5.8; parity goldens).

The flow: create (needs a confirmation for an existing language) → get → edit → start the
review → review (fake probes) → review narrowed by filters → resolve → refused choices →
exclude (asked, then confirmed) → list → delete (asked, then confirmed); plus the refusals of
each request.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import pytest

from support.batches import (
    EVERY_FILE,
    FILM,
    FIRST_BATCH_ID,
    MEDIA_ROOT,
    PODCAST,
    SILENT,
    batch_ports,
    batch_request,
    build_batch_tree,
    composition,
    create_request,
    extract,
    item_ids,
    list_request,
    response_of,
    transcribe,
)
from support.golden import assert_golden
from support.parity import Exchange, Template, run_parity
from transcribe_ui.cli.constants import EXIT_ERROR, EXIT_INVALID_REQUEST, EXIT_NEEDS_CONFIRMATION, EXIT_OK

# Target languages of the flow.
EN = "en"
HU = "hu"
# Index of the flow step whose response lists the items the later steps refer to (`edit`).
EDIT_STEP = 3
# Builds a request document from the item ids (by file name) of the edited batch.
type FromIds = Callable[[dict[str, str]], str]


@dataclass(frozen=True)
class WithIds:
    """A template that takes the item ids from the outcome of the `edit` step."""

    build: FromIds

    def __call__(self, exchanges: list[Exchange]) -> str:
        """Return the request document."""
        return self.build(item_ids(exchanges[EDIT_STEP]))


def _resolve(ids: dict[str, str]) -> str:
    choices = [extract(ids[FILM], EN, 0), transcribe(ids[FILM], HU, 1), extract(ids[SILENT], EN, 0)]
    return batch_request("resolve_review", choices=choices)


def _resolve_bitmap(ids: dict[str, str]) -> str:
    choices = [transcribe(ids[FILM], EN, 0), extract(ids[FILM], HU, 1)]
    return batch_request("resolve_review", choices=choices)


def _resolve_audio_track(ids: dict[str, str]) -> str:
    return batch_request("resolve_review", choices=[transcribe(ids[PODCAST], EN, 0)])


def _exclude(ids: dict[str, str], *, confirm: bool) -> str:
    return batch_request("exclude_items", item_ids=[ids[PODCAST]], confirm=confirm)


def _exclude_asking(ids: dict[str, str]) -> str:
    return _exclude(ids, confirm=False)


def _exclude_confirmed(ids: dict[str, str]) -> str:
    return _exclude(ids, confirm=True)


def _edit(*languages: str, paths: tuple[str, ...] = (MEDIA_ROOT,), **fields: object) -> str:
    edited = composition(*languages, paths=paths, **fields)
    return json.dumps({"action": "edit_batch", "batch_id": FIRST_BATCH_ID, **edited})


EDITED_PATHS = (f"{MEDIA_ROOT}/Movies", f"{MEDIA_ROOT}/Audio/{PODCAST}")
PODCAST_IN_ENGLISH = MappingProxyType({"path": f"{MEDIA_ROOT}/Audio/{PODCAST}", "languages": (EN,)})
FLOW: tuple[tuple[str, Template], ...] = (
    ("create_needs_confirmation", create_request(EN, HU)),
    ("create", create_request(EN, HU, confirm_existing_languages=True)),
    ("get", batch_request("get_batch")),
    (
        "edit",
        _edit(EN, HU, paths=EDITED_PATHS, item_languages=[dict(PODCAST_IN_ENGLISH)], confirm_existing_languages=True),
    ),
    ("start_review", batch_request("start_review")),
    ("review", batch_request("review_batch")),
    ("review_filtered", batch_request("review_batch", filters={"languages": [HU], "folder": f"{MEDIA_ROOT}/Movies"})),
    ("resolve", WithIds(_resolve)),
    ("resolve_bitmap_refused", WithIds(_resolve_bitmap)),
    ("resolve_audio_track_refused", WithIds(_resolve_audio_track)),
    ("exclude_needs_confirmation", WithIds(_exclude_asking)),
    ("exclude", WithIds(_exclude_confirmed)),
    ("list", list_request()),
    ("hide_refused", batch_request("set_batch_hidden", hidden=True)),
    ("edit_refused", _edit(EN)),
    ("delete_needs_confirmation", batch_request("delete_batch")),
    ("delete", batch_request("delete_batch", confirm=True)),
    ("list_empty", list_request(include_hidden=True)),
)
# Names of the flow steps, in order.
STEP_NAMES = tuple(name for name, _template in FLOW)


@pytest.fixture
def flow(tmp_path: Path) -> dict[str, Exchange]:
    """Run the whole flow through both legs; return each step's outcome by name."""
    templates = [template for _name, template in FLOW]
    exchanges = run_parity(tmp_path, templates, prepare=build_batch_tree, ports=batch_ports)
    return dict(zip(STEP_NAMES, exchanges, strict=True))


def test_flow_matches_goldens(flow: dict[str, Exchange]) -> None:
    """Every step of the flow gives the same response through the CLI and the backend, as recorded."""
    for number, name in enumerate(STEP_NAMES, start=1):
        assert_golden(f"batch_flow_{number:02d}_{name}.json", flow[name].golden())


def test_flow_exit_codes(flow: dict[str, Exchange]) -> None:
    """The confirmation, the successes and the refusals have their exit codes."""
    refused = ("resolve_bitmap_refused", "resolve_audio_track_refused", "hide_refused", "edit_refused")
    assert flow["create_needs_confirmation"].exit_code == EXIT_NEEDS_CONFIRMATION
    assert {flow[name].exit_code for name in refused} == {EXIT_ERROR}
    others = {flow[name].exit_code for name in STEP_NAMES if name not in refused}
    assert others == {EXIT_OK, EXIT_NEEDS_CONFIRMATION}


def test_flow_outcomes(flow: dict[str, Exchange]) -> None:
    """The flow's key outcomes, asserted directly (the goldens hold the details)."""
    review = response_of(flow["resolve"])["review"]
    assert isinstance(review, dict)
    created = sorted(item_ids(flow["create"]))
    edited = sorted(item_ids(flow["edit"]))
    assert (created, edited) == (sorted(EVERY_FILE), sorted((FILM, PODCAST, SILENT)))
    assert review["complete"] is True
    assert not response_of(flow["list_empty"])["batches"]


@pytest.mark.parametrize(
    ("name", "document"),
    [
        ("nothing_selected", create_request(EN, paths=(f"{MEDIA_ROOT}/Empty",))),
        (
            "path_not_selected",
            create_request(EN, item_languages=[{"path": f"{MEDIA_ROOT}/Nowhere.mkv", "languages": [HU]}]),
        ),
        ("unknown_language", create_request("xx")),
        ("duplicate_language", create_request(EN, EN)),
        ("not_found", batch_request("get_batch")),
        ("invalid_id", batch_request("get_batch", batch_id="batch_1")),
    ],
)
def test_refusals(tmp_path: Path, name: str, document: str) -> None:
    """Invalid requests and unknown batches are refused the same way through the CLI and the backend."""
    exchange = run_parity(tmp_path, [document], prepare=build_batch_tree, ports=batch_ports)[0]
    assert exchange.exit_code in {EXIT_ERROR, EXIT_INVALID_REQUEST}
    assert_golden(f"batch_refused_{name}.json", exchange.golden())
