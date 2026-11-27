"""The stored composition and the batch confirmations (plan m6 M6.1).

`create_batch` and `edit_batch` keep the selection they were sent, and `get_batch` returns it, so
"Edit selection" reopens it; `delete_batch` and `exclude_items` ask first and act once the
confirmation is sent back as every frontend does (each field set to its `set_to`).
"""

import json
from types import MappingProxyType

from support.batch_backend import ids_of, run, run_ok
from support.batches import FILM, FIRST_BATCH_ID, MEDIA_ROOT, PODCAST, batch_request, composition, create_request
from support.confirm import confirmed_document
from support.parity import Leg
from transcribe_ui.batches.payloads import BatchPayload
from transcribe_ui.batches.responses import BatchSelection
from transcribe_ui.state.models import BatchRow

# Languages of the batch.
EN = "en"
HU = "hu"
# The podcast's own languages (`item_languages`).
PODCAST_LANGUAGES = MappingProxyType({"path": f"{MEDIA_ROOT}/Audio/{PODCAST}", "languages": (EN,)})


def _confirmed(leg: Leg, document: str) -> dict[str, object]:
    """Send the request, which must ask; send it again confirmed; return the confirmed answer."""
    asked = run(leg, document)
    assert asked.status == "needs_confirmation", asked
    return run_ok(leg, confirmed_document(document, asked))


def _stored(leg: Leg) -> BatchSelection:
    batch = run_ok(leg, batch_request("get_batch"))["batch"]
    assert isinstance(batch, dict)
    return BatchSelection.model_validate(batch["composition"])


def _selection(leg: Leg, fields: dict[str, object]) -> BatchSelection:
    """Return the selection of a request's composition fields (`<root>` replaced), as it is stored."""
    document = json.loads(leg.document(json.dumps(fields)))
    kept = {key: document[key] for key in ("scan", "operations", "languages")}
    return BatchSelection.model_validate({**kept, "item_languages": document.get("item_languages", [])})


def test_create_and_edit_keep_the_composition(batch_leg: Leg) -> None:
    """The created batch returns its selection, an edit replaces it, and the stored one reopens as an edit."""
    _confirmed(batch_leg, create_request(EN, HU, scan_id=None))
    assert _stored(batch_leg) == _selection(batch_leg, composition(EN, HU))
    edited_fields = composition(EN, paths=(f"{MEDIA_ROOT}/Audio",), item_languages=[dict(PODCAST_LANGUAGES)])
    run_ok(batch_leg, json.dumps({"action": "edit_batch", "batch_id": FIRST_BATCH_ID, **edited_fields}))
    stored = _stored(batch_leg)
    assert stored == _selection(batch_leg, edited_fields)
    # "Edit selection": the stored composition sent back as it is gives the same batch again.
    reopened = {"action": "edit_batch", "batch_id": FIRST_BATCH_ID, **stored.model_dump(mode="json")}
    run_ok(batch_leg, json.dumps(reopened))
    assert _stored(batch_leg) == stored


def test_delete_asks_then_deletes(batch_leg: Leg) -> None:
    """`delete_batch` answers with its confirmation (nothing deleted), then deletes once confirmed."""
    _confirmed(batch_leg, create_request(EN, HU))
    asked = run(batch_leg, batch_request("delete_batch"))
    assert asked.status == "needs_confirmation"
    confirmation = asked.confirmations[0]
    assert (confirmation.field, confirmation.set_to, confirmation.details["phase"]) == ("confirm", True, "composing")
    assert run_ok(batch_leg, batch_request("get_batch"))["batch"]
    deleted = run_ok(batch_leg, confirmed_document(batch_request("delete_batch"), asked))
    assert deleted["batch_id"] == FIRST_BATCH_ID
    listed = run_ok(batch_leg, json.dumps({"action": "list_batches", "include_hidden": True}))
    assert listed["batches"] == []


def test_exclusion_asks_then_excludes(batch_leg: Leg) -> None:
    """`exclude_items` in review asks (no audio to delete yet), then removes the item once confirmed."""
    _confirmed(batch_leg, create_request(EN, HU))
    run_ok(batch_leg, batch_request("start_review"))
    film = ids_of(batch_leg)[FILM]
    document = batch_request("exclude_items", item_ids=[film])
    asked = run(batch_leg, document)
    assert asked.status == "needs_confirmation"
    details = asked.confirmations[0].details
    assert (details["items"], details["deletes_uploaded_audio"]) == (1, False)
    assert FILM in ids_of(batch_leg)
    run_ok(batch_leg, confirmed_document(document, asked))
    assert FILM not in ids_of(batch_leg)


def test_unreadable_composition_is_left_out(batch_leg: Leg) -> None:
    """A stored selection with a language no longer known still lists; its detail has no composition."""
    _confirmed(batch_leg, create_request(EN, HU))
    with batch_leg.ports.state.session() as session:
        row = session.get(BatchRow, FIRST_BATCH_ID)
        assert row is not None
        payload = BatchPayload.model_validate(row.payload)
        assert payload.composition is not None
        unknown = payload.composition.model_copy(update={"languages": ("xx",)})
        row.payload = payload.model_copy(update={"composition": unknown}).model_dump(mode="json")
    listed = run_ok(batch_leg, json.dumps({"action": "list_batches"}))["batches"]
    assert [summary["batch_id"] for summary in listed] == [FIRST_BATCH_ID]
    batch = run_ok(batch_leg, batch_request("get_batch"))["batch"]
    assert isinstance(batch, dict)
    assert batch["composition"] is None
