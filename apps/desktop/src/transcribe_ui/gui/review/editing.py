"""The user's edits on the review page: choices per transcription, and files ticked for removal.

Qt-free. A choice is one of the options of its transcription (`options.py`); it waits in the
draft until Apply. "Transcribe all shown" and "Extract all shown" choose for every transcription
the embedded-subtitle view shows (narrowed by the filters, so per folder or per language): the
backend's suggestion (`ReviewFile.suggestions`: the pre-selected track, the text subtitle tagged
with the language), where it is one of the options. Ticks for "Remove from job" are per file.
"""

from collections.abc import Callable

from transcribe_ui.batches.classes import ExtractChoice, ReviewChoice, TranscribeChoice
from transcribe_ui.batches.responses import BatchReview, JobView, ReviewFile, ReviewSuggestion
from transcribe_ui.gui.review.draft import ChoiceDraft
from transcribe_ui.gui.review.loading import ReviewLoader
from transcribe_ui.gui.review.options import current_choice, job_options

# Chooses for a transcription from its file, its job, the choice it shows and the stored one (None: no change).
type Suggested = Callable[[ReviewFile, JobView, ReviewChoice | None, ReviewChoice | None], ReviewChoice | None]


def review_files(review: BatchReview | None) -> dict[str, ReviewFile]:
    """Return every file the review shows, by item id."""
    if review is None:
        return {}
    files = (*review.subtitle_view, *review.track_view, *review.unresolved)
    return {review_file.item_id: review_file for review_file in files}


def _job(review_file: ReviewFile, language: str) -> JobView | None:
    return next((job for job in review_file.jobs if job.language == language), None)


def _suggestion(review_file: ReviewFile, language: str) -> ReviewSuggestion | None:
    return next((shown for shown in review_file.suggestions if shown.language == language), None)


class ChoiceEditor:
    """The choices not applied yet and the files ticked for removal."""

    def __init__(self, loader: ReviewLoader, notify: Callable[[], None]) -> None:
        """Edit the review `loader` shows now; `notify` follows every edit."""
        self._loader = loader
        # Tells the page that its display data changed.
        self.notify = notify
        self.draft = ChoiceDraft()
        self._ticked: dict[str, None] = {}

    def choose(self, item_id: str, language: str, index: int) -> None:
        """Choose the option at `index` for a transcription."""
        review_file = review_files(self._loader.loaded.review).get(item_id)
        job = None if review_file is None else _job(review_file, language)
        if review_file is None or job is None:
            return
        options = job_options(review_file, language)
        if 0 <= index < len(options):
            self.draft.choose(options[index].choice, current_choice(item_id, job))
            self.notify()

    def transcribe_shown(self) -> None:
        """Choose transcribing (from the suggested track) for every transcription of the embedded-subtitle view."""
        self._choose_shown(_transcribe_suggestion)

    def extract_shown(self) -> None:
        """Choose extracting the suggested text subtitle for every transcription of the embedded-subtitle view."""
        self._choose_shown(_extract_suggestion)

    def discard(self) -> None:
        """Drop every choice not applied yet."""
        self.draft.clear()
        self.notify()

    def tick(self, item_id: str, *, ticked: bool) -> None:
        """Tick or untick a file for "Remove from job"."""
        if ticked:
            self._ticked[item_id] = None
        else:
            self._ticked.pop(item_id, None)
        self.notify()

    def ticked(self) -> tuple[str, ...]:
        """Return the files ticked for removal that the review still shows, in the order ticked."""
        shown = review_files(self._loader.loaded.review)
        return tuple(item_id for item_id in self._ticked if item_id in shown)

    def forget_items(self, item_ids: tuple[str, ...]) -> None:
        """Drop the ticks and choices of files that left the job."""
        for item_id in item_ids:
            self._ticked.pop(item_id, None)
        self.draft.drop_items(item_ids)

    def reset(self) -> None:
        """Drop every choice and tick (another job)."""
        self.draft.clear()
        self._ticked = {}

    def _choose_shown(self, suggested: Suggested) -> None:
        review = self._loader.loaded.review
        if review is None:
            return
        for review_file in review.subtitle_view:
            for job in review_file.jobs:
                stored = current_choice(review_file.item_id, job)
                shown = self.draft.shown((review_file.item_id, job.language), stored)
                choice = suggested(review_file, job, shown, stored)
                options = [option.choice for option in job_options(review_file, job.language)]
                if choice is not None and choice in options:
                    self.draft.choose(choice, stored)
        self.notify()


def _transcribe_suggestion(
    review_file: ReviewFile,
    job: JobView,
    shown: ReviewChoice | None,
    stored: ReviewChoice | None,
) -> ReviewChoice | None:
    """Return transcribing from the stored track, else the suggested one (None: it transcribes already)."""
    if isinstance(shown, TranscribeChoice):
        return None
    if isinstance(stored, TranscribeChoice):
        return stored
    suggestion = _suggestion(review_file, job.language)
    track = None if suggestion is None else suggestion.track_index
    return TranscribeChoice(kind="transcribe", item_id=review_file.item_id, language=job.language, track_index=track)


def _extract_suggestion(
    review_file: ReviewFile,
    job: JobView,
    shown: ReviewChoice | None,
    _stored: ReviewChoice | None,
) -> ReviewChoice | None:
    """Return extracting the suggested text subtitle (None: it extracts already, or nothing is suggested)."""
    suggestion = _suggestion(review_file, job.language)
    if isinstance(shown, ExtractChoice) or suggestion is None or suggestion.stream_index is None:
        return None
    return ExtractChoice(
        kind="extract",
        item_id=review_file.item_id,
        language=job.language,
        stream_index=suggestion.stream_index,
    )
