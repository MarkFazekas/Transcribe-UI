"""One test backend of the batch tests, and helpers that send requests to it (plan m5 M5.8)."""

import json
from collections.abc import Sequence
from typing import Any

from support.batches import FIRST_BATCH_ID, MEDIA_ROOT, batch_request, create_request, item_ids
from support.parity import Exchange, Leg
from transcribe_ui.api.classes import Response
from transcribe_ui.api.ports import CancelFlag, DiscardProgress, RequestContext
from transcribe_ui.media.fake import FakeMediaTools
from transcribe_ui.state.models import BatchRow, ItemRow

# A response document as a dict.
type Document = dict[str, Any]
# The `review_batch` request of the first batch.
REVIEW = batch_request("review_batch")


def run(leg: Leg, template: str) -> Response:
    """Send a request document (with `<root>`) to the leg's backend."""
    return leg.backend.execute_json(leg.document(template))


def run_ok(leg: Leg, template: str) -> Document:
    """Send a request that must succeed; return its response as a dict."""
    response = run(leg, template)
    assert response.status == "ok", response
    document: Document = json.loads(response.model_dump_json())
    return document


def error_of(response: Response) -> tuple[str, Document]:
    """Return the error code and details of an error response."""
    assert response.status == "error", response
    return response.error.code, dict(response.error.details)


def error_code(response: Response) -> str:
    """Return the error code of an error response."""
    return error_of(response)[0]


def ids_of(leg: Leg, batch_id: str = FIRST_BATCH_ID) -> dict[str, str]:
    """Return file name -> item id of a batch."""
    detail = run_ok(leg, batch_request("get_batch", batch_id))
    return item_ids(Exchange(json.dumps(detail), (), 0))


def media_of(leg: Leg) -> FakeMediaTools:
    """Return the leg's fake media tools."""
    media = leg.ports.media
    assert isinstance(media, FakeMediaTools)
    return media


def probes_of(leg: Leg) -> list[str]:
    """Return the probe calls of the leg's fake media tools so far."""
    return [call for call in media_of(leg).calls if call.startswith("probe")]


def batch_row(leg: Leg, batch_id: str = FIRST_BATCH_ID) -> BatchRow:
    """Return a detached copy of the batch's row."""
    with leg.ports.state.session() as session:
        row = session.get(BatchRow, batch_id)
        assert row is not None
        session.expunge(row)
        return row


def item_phase(leg: Leg, item_id: str) -> str:
    """Return an item's phase."""
    with leg.ports.state.session() as session:
        row = session.get(ItemRow, item_id)
        assert row is not None
        return row.phase


def context_of(leg: Leg) -> RequestContext:
    """Return a request context on the leg's ports."""
    return RequestContext(leg.ports, DiscardProgress(), CancelFlag())


def start(leg: Leg, *languages: str, paths: Sequence[str] = (MEDIA_ROOT,)) -> None:
    """Create the first batch (existing languages confirmed) and start its review."""
    run_ok(leg, create_request(*languages, paths=paths, confirm_existing_languages=True))
    run_ok(leg, batch_request("start_review"))
