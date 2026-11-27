"""The review step's requests: `review_batch` and `resolve_review` (architecture §3.3 "Review step").

Both serve the items in review: every item of a `reviewing` batch, and the items of an
`uploading` batch that went back to review (plan m5 §1 "Per-item re-review"). In an `uploading`
batch an item whose upload has started (it has `audio_objects` or `extracted_subtitles` rows)
cannot change. Choices are validated against the probed file (`batches/choices.py`) and
applied all or none.
"""

from sqlalchemy.orm import Session

from transcribe_contracts.classes import TranscriptionMode
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.choices import choice_problem
from transcribe_ui.batches.classes import (
    ExtractChoice,
    ResolveReviewRequest,
    ReviewBatchRequest,
    ReviewChoice,
    ReviewFilter,
)
from transcribe_ui.batches.constants import (
    CHOICE_MESSAGES,
    REASON_NO_SUCH_ITEM,
    REASON_NO_SUCH_JOB,
    REASON_NOT_IN_REVIEW,
    REASON_UPLOAD_STARTED,
    REVIEW_PHASES,
    UPLOADING_PHASES,
)
from transcribe_ui.batches.errors import ChoiceInvalidError
from transcribe_ui.batches.payloads import JobKind, JobPayload
from transcribe_ui.batches.phases import load_batch, require_phase_name, set_review_phase, touch_batch
from transcribe_ui.batches.probing import probe_waiting_items
from transcribe_ui.batches.records import StoredItem, StoredJob, items_with_uploads, stored_items
from transcribe_ui.batches.responses import BatchReview, ResolveReviewResponse, ReviewBatchResponse
from transcribe_ui.batches.views import build_review
from transcribe_ui.discovery.rules import PathRules
from transcribe_ui.state.models import JobRow


def _review(session: Session, batch_id: str, filters: ReviewFilter, rules: PathRules) -> BatchReview:
    return build_review(session, load_batch(session, batch_id), filters, rules)


def review_batch(request: ReviewBatchRequest, context: RequestContext) -> ReviewBatchResponse:
    """Probe the items waiting for review, then return the review views narrowed by the filters."""
    state = context.ports.state
    batch_id = request.batch_id
    with state.locks.batch(batch_id):
        probe_waiting_items(context, batch_id, request.action)
        with state.session() as session:
            review = _review(session, batch_id, request.filters, context.ports.path_rules)
    return ReviewBatchResponse(review=review)


def _chosen(choice: ReviewChoice, mode: TranscriptionMode) -> tuple[JobKind, JobPayload]:
    if isinstance(choice, ExtractChoice):
        return "extract", JobPayload(mode=mode, stream_index=choice.stream_index)
    return "transcribe", JobPayload(mode=mode, track_index=choice.track_index)


def _invalid(index: int, reason: str) -> ChoiceInvalidError:
    return ChoiceInvalidError(index, reason, CHOICE_MESSAGES[reason])


# A validated choice: the job id and its new kind and payload.
type Change = tuple[int, JobKind, JobPayload]


# Items of a batch by item id.
type Members = dict[str, StoredItem]
# Ids of the items whose upload has started (only in an `uploading` batch).
type Started = frozenset[str]


def _unchangeable(member: StoredItem, started: Started) -> str | None:
    """Return why the item's choices cannot change, or None."""
    if not member.in_review():
        return REASON_NOT_IN_REVIEW
    return REASON_UPLOAD_STARTED if member.item_id in started else None


def _target(index: int, choice: ReviewChoice, members: Members, started: Started) -> tuple[StoredItem, StoredJob]:
    member = members.get(choice.item_id)
    if member is None:
        raise _invalid(index, REASON_NO_SUCH_ITEM)
    refusal = _unchangeable(member, started)
    if refusal is not None:
        raise _invalid(index, refusal)
    job = member.job(choice.language)
    if job is None:
        raise _invalid(index, REASON_NO_SUCH_JOB)
    return member, job


def _checked(index: int, choice: ReviewChoice, members: Members, started: Started) -> Change:
    """Return the job id and the new choice of a valid choice.

    Raises:
        ChoiceInvalidError: If the choice does not fit its item.
    """
    member, job = _target(index, choice, members, started)
    kind, payload = _chosen(choice, job.payload.mode)
    problem = choice_problem(member.payload, kind, payload)
    if problem is not None:
        raise _invalid(index, problem)
    return job.row.job_id, kind, payload


def _changes(session: Session, request: ResolveReviewRequest, phase: str) -> list[Change]:
    """Return the change of every choice.

    Raises:
        ChoiceInvalidError: If a choice does not fit its item (nothing is changed then).
    """
    batch_id = request.batch_id
    members = {member.item_id: member for member in stored_items(session, batch_id)}
    started: Started = frozenset()
    if phase in UPLOADING_PHASES:
        started = items_with_uploads(session, batch_id)
    numbered = enumerate(request.choices)
    return [_checked(index, choice, members, started) for index, choice in numbered]


def _apply(session: Session, request: ResolveReviewRequest, phase: str) -> None:
    """Check every choice, then apply them all and update the items' phases (nothing on a refusal).

    Raises:
        ChoiceInvalidError: If a choice does not fit its item.
    """
    for job_id, kind, payload in _changes(session, request, phase):
        job_row = session.get(JobRow, job_id)
        assert job_row is not None, "the job was just read"
        job_row.kind = kind
        job_row.payload = payload.model_dump(mode="json")
    session.flush()
    for item_id in sorted({choice.item_id for choice in request.choices}):
        set_review_phase(session, item_id)


def resolve_review(request: ResolveReviewRequest, context: RequestContext) -> ResolveReviewResponse:
    """Apply the choices (all or none), update the items' phases and return the unfiltered review."""
    state = context.ports.state
    batch_id = request.batch_id
    now = context.ports.clock.now()
    with state.locks.batch(batch_id), state.session() as session:
        phase = load_batch(session, batch_id).phase
        require_phase_name(batch_id, phase, REVIEW_PHASES)
        _apply(session, request, phase)
        touch_batch(session, batch_id, (phase,), now=now)
        review = _review(session, batch_id, ReviewFilter(), context.ports.path_rules)
    return ResolveReviewResponse(review=review)
