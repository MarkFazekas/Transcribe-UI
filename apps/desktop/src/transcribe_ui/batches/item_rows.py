"""The rows of a new selection (`create_batch`, `edit_batch`): the batch payload, the items and their jobs."""

from sqlalchemy.orm import Session

from transcribe_ui.api.ports import IdSource
from transcribe_ui.batches.classes import BatchComposition
from transcribe_ui.batches.composition import Composition, Languages
from transcribe_ui.batches.constants import COMPOSING, DEFAULT_MODE, JOB_PENDING
from transcribe_ui.batches.payloads import BatchPayload, ItemPayload, ItemPhase, JobPayload, StoredComposition
from transcribe_ui.state.models import ItemRow, JobRow, Payload


def batch_payload(composition: Composition, request: BatchComposition) -> dict[str, object]:
    """Return the batch payload of the composition, with the request's selection kept."""
    payload = BatchPayload(
        scan_root=composition.scan_root,
        path_flavor="windows" if composition.path_flavor == "windows" else "posix",
        languages=request.languages,
        composition=StoredComposition(
            scan=request.scan,
            operations=request.operations,
            languages=request.languages,
            item_languages=request.item_languages,
        ),
    )
    return payload.model_dump(mode="json")


def add_items(session: Session, batch_id: str, composition: Composition, ids: IdSource) -> None:
    """Insert the composition's items in `composing`, each with a pending `transcribe` job per language."""
    job = JobPayload(mode=DEFAULT_MODE).model_dump(mode="json")
    members = composition.members
    rows = [_item_row(batch_id, ids.new_id(), new.payload) for new in members]
    session.add_all(rows)
    # The tables have no ORM relationships, so the unit of work does not order inserts by foreign key.
    session.flush()
    for row, new in zip(rows, members, strict=True):
        session.add_all(_job_rows(row.item_id, batch_id, new.languages, job))
    session.flush()


def _item_row(batch_id: str, item_id: str, payload: ItemPayload) -> ItemRow:
    phase: ItemPhase = COMPOSING
    stored = payload.model_dump(mode="json")
    return ItemRow(item_id=item_id, batch_id=batch_id, phase=phase, payload=stored)


def _job_rows(item_id: str, batch_id: str, languages: Languages, job: Payload) -> list[JobRow]:
    rows = []
    for language in sorted(languages):
        row = JobRow(item_id=item_id, batch_id=batch_id, language=language, payload=job)
        row.kind = "transcribe"
        row.status = JOB_PENDING
        rows.append(row)
    return rows
