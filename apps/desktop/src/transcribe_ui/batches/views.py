"""The review views: files with embedded subtitles, video files with several audio tracks, unresolved files."""

from sqlalchemy.orm import Session

from transcribe_ui.batches.classes import ReviewFilter
from transcribe_ui.batches.records import StoredItem, job_view, stored_items
from transcribe_ui.batches.responses import BatchReview, ReviewFile
from transcribe_ui.discovery.rules import PathRules
from transcribe_ui.state.models import BatchRow


def _under(rules: PathRules, path: str, folder: str) -> bool:
    folder_keys = [rules.key(part) for part in rules.parts(folder)]
    path_keys = [rules.key(part) for part in rules.parts(path)]
    return path_keys[: len(folder_keys)] == folder_keys


def review_file(member: StoredItem, languages: frozenset[str]) -> ReviewFile:
    """Return an item as the review views show it, with only the jobs of `languages` (all when empty)."""
    payload = member.payload
    probe = payload.probe
    shown = languages or member.languages()
    jobs = [job for job in member.jobs if job.row.language in shown]
    return ReviewFile(
        item_id=member.item_id,
        path=payload.path,
        name=payload.name,
        kind=payload.kind,
        phase=member.phase,
        problem=payload.problem,
        audio_tracks=() if probe is None else probe.audio_tracks,
        subtitle_streams=() if probe is None else probe.subtitle_streams,
        jobs=tuple(job_view(payload, job) for job in jobs),
    )


def _shown(member: StoredItem, filters: ReviewFilter, rules: PathRules) -> bool:
    wanted = frozenset(filters.languages)
    if wanted and not wanted & member.languages():
        return False
    folder = filters.folder
    return folder is None or _under(rules, member.payload.path, folder)


def _files(members: list[StoredItem], languages: frozenset[str]) -> tuple[ReviewFile, ...]:
    return tuple(review_file(member, languages) for member in members)


def build_review(session: Session, row: BatchRow, filters: ReviewFilter, rules: PathRules) -> BatchReview:
    """Return the review views of the batch's items in review, narrowed by the filters."""
    in_review = [member for member in stored_items(session, row.batch_id) if member.in_review()]
    unresolved = [member for member in in_review if not member.resolved()]
    shown = [member for member in in_review if _shown(member, filters, rules)]
    languages = frozenset(filters.languages)
    return BatchReview(
        batch_id=row.batch_id,
        phase=row.phase,
        items_in_review=len(in_review),
        items_unresolved=len(unresolved),
        complete=not unresolved,
        subtitle_view=_files([member for member in shown if member.has_subtitles()], languages),
        track_view=_files([member for member in shown if member.has_track_choice()], languages),
        unresolved=_files([member for member in shown if not member.resolved()], languages),
    )
