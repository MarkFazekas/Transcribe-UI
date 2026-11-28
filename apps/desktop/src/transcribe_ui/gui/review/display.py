"""Display data of the review page's rows (architecture §3.1 "GUI" "Review and upload").

Qt-free. A row's shape comes from the review index (`index.py`, built once per review); what the
user's edits change is read here per row when a table asks for it: the option the choice shows
(the draft's, else the stored one), the status (changed, a choice missing, or why there is
nothing to choose) and the Remove tick.
"""

from dataclasses import dataclass

from transcribe_ui.gui.review.constants import ReviewTab
from transcribe_ui.gui.review.index import RowShape
from transcribe_ui.gui.review.options import option_index
from transcribe_ui.gui.review.viewmodel import ReviewModel
from transcribe_ui.i18n.catalog import tr


@dataclass(frozen=True)
class ReviewRow:
    """A row of a review table: its shape and what the user's edits change."""

    shape: RowShape
    # The option the choice shows (None: none of them, the choice is missing).
    chosen: int | None
    status: str
    ticked: bool


def row_state(model: ReviewModel, shape: RowShape) -> ReviewRow:
    """Return a row with what the user's edits show in it."""
    chosen = None
    status = shape.note
    if shape.language is not None and shape.options:
        key = (shape.item_id, shape.language)
        draft = model.editor.draft
        chosen = option_index(shape.options, draft.shown(key, shape.stored))
        status = _choice_status(changed=draft.changed(key), chosen=chosen)
    return ReviewRow(shape, chosen, status, ticked=shape.item_id in model.editor.ticks)


def _choice_status(*, changed: bool, chosen: int | None) -> str:
    if chosen is None:
        return tr("Choose how to make this subtitle")
    return tr("Changed; not applied yet") if changed else ""


def review_rows(model: ReviewModel, tab: ReviewTab) -> tuple[ReviewRow, ...]:
    """Return every row of a review table with its state (for a whole table at once; the tables read row by row)."""
    shapes = model.editor.views.current().tabs[tab].shapes
    return tuple(row_state(model, shape) for shape in shapes)


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
