"""Display data of the review page (architecture §3.1 "GUI" "Review and upload").

Qt-free. Rows come from the backend's review views (a row per transcription of a file with
embedded subtitles or with several audio tracks, a row per file that needs attention), texts
from the catalog. Only presentation is decided here: labels, which choice a combo box shows,
the folder column, and the hints of the buttons, whose states follow the allowed actions.
"""

from dataclasses import dataclass

from transcribe_ui.batches.responses import JobView, ReviewFile
from transcribe_ui.gui.review.constants import LIST_SEPARATOR, ReviewTab
from transcribe_ui.gui.review.folders import relative_folder
from transcribe_ui.gui.review.options import current_choice, job_options, option_index, stream_text, track_text
from transcribe_ui.gui.review.viewmodel import ReviewModel
from transcribe_ui.i18n.catalog import tr
from transcribe_ui.i18n.texts import code_text


@dataclass(frozen=True)
class RowShape:
    """What a row of a review table shows that only a new review changes (the widgets are rebuilt for it)."""

    item_id: str
    # The transcription's language; None for a row of a file that needs attention.
    language: str | None
    file_name: str
    folder: str
    options: tuple[str, ...]
    details: str


@dataclass(frozen=True)
class ReviewRow:
    """A row of a review table: its shape and what the user's edits change in place."""

    shape: RowShape
    # The option the combo box shows (None: none of them, the choice is missing).
    chosen: int | None
    status: str
    ticked: bool


def _folder(model: ReviewModel, path: str) -> str:
    detail = model.loader.loaded.detail
    return "" if detail is None else relative_folder(detail, path)


def _streams_text(review_file: ReviewFile) -> str:
    return "; ".join(stream_text(stream) for stream in review_file.subtitle_streams)


def _tracks_text(review_file: ReviewFile) -> str:
    return "; ".join(track_text(track) for track in review_file.audio_tracks)


def _choice_status(*, changed: bool, chosen: int | None) -> str:
    if chosen is None:
        return tr("Choose how to make this subtitle")
    return tr("Changed; not applied yet") if changed else ""


def _choice_row(
    model: ReviewModel, review_file: ReviewFile, job: JobView, details: str, ticked: frozenset[str]
) -> ReviewRow:
    item_id = review_file.item_id
    options = job_options(review_file, job.language)
    key = (item_id, job.language)
    draft = model.editor.draft
    chosen = option_index(options, draft.shown(key, current_choice(item_id, job)))
    shape = RowShape(
        item_id=item_id,
        language=job.language,
        file_name=review_file.name,
        folder=_folder(model, review_file.path),
        options=tuple(option.label for option in options),
        details=details,
    )
    status = _choice_status(changed=draft.changed(key), chosen=chosen)
    return ReviewRow(shape, chosen, status, ticked=item_id in ticked)


def _file_rows(model: ReviewModel, review_file: ReviewFile, details: str, ticked: frozenset[str]) -> list[ReviewRow]:
    return [_choice_row(model, review_file, job, details, ticked) for job in review_file.jobs]


def _attention_text(review_file: ReviewFile) -> str:
    problem = review_file.problem
    if problem is not None:
        return code_text("item_problem", problem.code, {}, problem.message)
    missing = [job.language for job in review_file.jobs if not job.resolved]
    without = [language for language in missing if not job_options(review_file, language)]
    if without:
        text = tr("Nothing to transcribe or extract for {languages}: remove the file from the job.")
        return text.format(languages=LIST_SEPARATOR.join(without))
    if missing:
        return tr("Choose a track or a subtitle for {languages}.").format(languages=LIST_SEPARATOR.join(missing))
    return tr("Not checked yet.")


def _attention_row(model: ReviewModel, review_file: ReviewFile, ticked: frozenset[str]) -> ReviewRow:
    shape = RowShape(
        item_id=review_file.item_id,
        language=None,
        file_name=review_file.name,
        folder=_folder(model, review_file.path),
        options=(),
        details=review_file.path,
    )
    return ReviewRow(shape, None, _attention_text(review_file), ticked=review_file.item_id in ticked)


def review_rows(model: ReviewModel, tab: ReviewTab) -> tuple[ReviewRow, ...]:
    """Return the rows of a review table (none before the review answered)."""
    review = model.loader.loaded.review
    if review is None:
        return ()
    ticked = frozenset(model.editor.ticked())
    if tab == "attention":
        return tuple(_attention_row(model, review_file, ticked) for review_file in review.unresolved)
    files = review.subtitle_view if tab == "subtitles" else review.track_view
    rows = []
    describe = _streams_text if tab == "subtitles" else _tracks_text
    for review_file in files:
        rows.extend(_file_rows(model, review_file, describe(review_file), ticked))
    return tuple(rows)


def tab_title(tab: ReviewTab, count: int) -> str:
    """Return a review table's tab title with its number of rows."""
    titles: dict[ReviewTab, str] = {
        "subtitles": tr("Embedded subtitles ({count})"),
        "tracks": tr("Audio tracks ({count})"),
        "attention": tr("Needs attention ({count})"),
    }
    return titles[tab].format(count=count)


def file_names(model: ReviewModel, item_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Return the names of the job's files (an id the job does not list stays an id)."""
    detail = model.loader.loaded.detail
    members = () if detail is None else detail.items
    listed = {member.item_id: member.name for member in members}
    return tuple(listed.get(item_id, item_id) for item_id in item_ids)
