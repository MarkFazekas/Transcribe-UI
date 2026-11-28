"""The rows of the review tables, built once per review (architecture §3.1 "GUI" "Review and upload").

Qt-free. A job may hold tens of thousands of files, so what a row shows that only a new review
changes (file, folder, language, the choices and their labels, the stored choice, the details)
is built once per review and kept, with the rows of each transcription and file, so an edit
finds the rows it changes without a scan. What the user's edits change (the choice shown, the
status, the Remove tick) is read per row when a table asks for it (`display.row_state`). The
folder filter's choices are built once per load of the job.
"""

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from transcribe_ui.batches.classes import ReviewChoice
from transcribe_ui.batches.responses import BatchDetail, BatchReview, JobView, ReviewFile
from transcribe_ui.gui.review.constants import LIST_SEPARATOR, TABS, ReviewTab
from transcribe_ui.gui.review.draft import ChoiceKey
from transcribe_ui.gui.review.folders import FolderChoice, folder_choices, relative_folder
from transcribe_ui.gui.review.loading import ReviewLoader
from transcribe_ui.gui.review.options import ChoiceOption, current_choice, job_options, stream_text, track_text
from transcribe_ui.i18n.catalog import tr
from transcribe_ui.i18n.texts import code_text


@dataclass(frozen=True)
class RowShape:
    """What a row of a review table shows that only a new review changes."""

    item_id: str
    # The transcription's language; None for a row of a file that needs attention.
    language: str | None
    file_name: str
    folder: str
    options: tuple[ChoiceOption, ...]
    # The choice stored in the job (None: none, or a file that needs attention).
    stored: ReviewChoice | None
    details: str
    # Why the row has nothing to choose (a file's problem, no track and no text subtitle), or "".
    note: str

    @property
    def labels(self) -> tuple[str, ...]:
        """Return the labels of the options, in order."""
        return tuple(option.label for option in self.options)


@dataclass(frozen=True)
class TableRows:
    """The rows of one review table, and where each transcription and each file is."""

    shapes: tuple[RowShape, ...] = ()
    by_key: Mapping[ChoiceKey, int] = field(default_factory=lambda: MappingProxyType({}))
    by_item: Mapping[str, tuple[int, ...]] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class ReviewIndex:
    """A review's files by item id, its transcriptions, and the rows of each table."""

    files: Mapping[str, ReviewFile]
    keys: frozenset[ChoiceKey]
    tabs: Mapping[ReviewTab, TableRows]


# The index of no review.
_NO_ROWS = MappingProxyType({tab: TableRows() for tab in TABS})
EMPTY_INDEX = ReviewIndex(MappingProxyType({}), frozenset(), _NO_ROWS)


def _streams_text(review_file: ReviewFile) -> str:
    return "; ".join(stream_text(stream) for stream in review_file.subtitle_streams)


def _tracks_text(review_file: ReviewFile) -> str:
    return "; ".join(track_text(track) for track in review_file.audio_tracks)


def no_options_text(languages: list[str]) -> str:
    """Return the note of transcriptions that can be neither transcribed nor extracted."""
    text = tr("Nothing to transcribe or extract for {languages}: remove the file from the job.")
    return text.format(languages=LIST_SEPARATOR.join(languages))


def attention_text(review_file: ReviewFile) -> str:
    """Return why a file needs attention: its problem, the languages it cannot make, or those without a choice."""
    problem = review_file.problem
    if problem is not None:
        return code_text("item_problem", problem.code, {}, problem.message)
    missing = [job.language for job in review_file.jobs if not job.resolved]
    without = [language for language in missing if not job_options(review_file, language)]
    if without:
        return no_options_text(without)
    if missing:
        return tr("Choose a track or a subtitle for {languages}.").format(languages=LIST_SEPARATOR.join(missing))
    return tr("Not checked yet.")


class _Builder:
    """Builds the rows of one review, with the folder column below the job's folder."""

    def __init__(self, detail: BatchDetail | None) -> None:
        self._detail = detail

    def folder(self, path: str) -> str:
        detail = self._detail
        return "" if detail is None else relative_folder(detail, path)

    def choice_row(self, review_file: ReviewFile, job: JobView, details: str) -> RowShape:
        options = job_options(review_file, job.language)
        return RowShape(
            item_id=review_file.item_id,
            language=job.language,
            file_name=review_file.name,
            folder=self.folder(review_file.path),
            options=options,
            stored=current_choice(review_file.item_id, job),
            details=details,
            note="" if options else no_options_text([job.language]),
        )

    def file_rows(self, review_file: ReviewFile, details: str) -> list[RowShape]:
        return [self.choice_row(review_file, job, details) for job in review_file.jobs]

    def attention_row(self, review_file: ReviewFile) -> RowShape:
        return RowShape(
            item_id=review_file.item_id,
            language=None,
            file_name=review_file.name,
            folder=self.folder(review_file.path),
            options=(),
            stored=None,
            details=review_file.path,
            note=attention_text(review_file),
        )

    def table(self, review: BatchReview, tab: ReviewTab) -> TableRows:
        shapes: list[RowShape] = []
        if tab == "attention":
            shapes.extend(self.attention_row(review_file) for review_file in review.unresolved)
        else:
            files = review.subtitle_view if tab == "subtitles" else review.track_view
            describe = _streams_text if tab == "subtitles" else _tracks_text
            for review_file in files:
                shapes.extend(self.file_rows(review_file, describe(review_file)))
        return _table_rows(tuple(shapes))


def _table_rows(shapes: tuple[RowShape, ...]) -> TableRows:
    by_key: dict[ChoiceKey, int] = {}
    by_item: dict[str, list[int]] = {}
    for row, shape in enumerate(shapes):
        if shape.language is not None:
            by_key[shape.item_id, shape.language] = row
        by_item.setdefault(shape.item_id, []).append(row)
    rows = {item_id: tuple(found) for item_id, found in by_item.items()}
    return TableRows(shapes, MappingProxyType(by_key), MappingProxyType(rows))


def _keys(files: Iterable[ReviewFile]) -> Iterator[ChoiceKey]:
    for review_file in files:
        for job in review_file.jobs:
            yield review_file.item_id, job.language


def build_index(review: BatchReview, detail: BatchDetail | None) -> ReviewIndex:
    """Return the index of a review: its files, its transcriptions and the rows of each table."""
    files: dict[str, ReviewFile] = {}
    for review_file in (*review.subtitle_view, *review.track_view, *review.unresolved):
        files[review_file.item_id] = review_file
    keys = frozenset(_keys(files.values()))
    builder = _Builder(detail)
    tabs = {tab: builder.table(review, tab) for tab in TABS}
    return ReviewIndex(MappingProxyType(files), keys, MappingProxyType(tabs))


class IndexCache:
    """The index of the review the page shows, rebuilt only when the review (or the job's folder) changes."""

    def __init__(self, loader: ReviewLoader) -> None:
        """Index what `loader` shows."""
        self._loader = loader
        self._index = EMPTY_INDEX
        self._indexed: tuple[BatchReview | None, str | None] = (None, None)
        self._folders: tuple[FolderChoice, ...] = ()
        self._folders_of: BatchDetail | None = None

    def current(self) -> ReviewIndex:
        """Return the index of the review shown now."""
        loaded = self._loader.loaded
        review = loaded.review
        detail = loaded.detail
        root = None if detail is None else detail.scan_root
        indexed = self._indexed
        if review is not indexed[0] or root != indexed[1]:
            self._indexed = (review, root)
            self._index = EMPTY_INDEX if review is None else build_index(review, detail)
        return self._index

    def folders(self) -> tuple[FolderChoice, ...]:
        """Return the folder filter's choices for the job shown (built once per load of the job)."""
        detail = self._loader.loaded.detail
        if detail is not self._folders_of:
            self._folders_of = detail
            self._folders = () if detail is None else folder_choices(detail)
        return self._folders
