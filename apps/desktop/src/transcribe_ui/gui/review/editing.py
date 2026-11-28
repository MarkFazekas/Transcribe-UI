"""The user's edits on the review page: choices per transcription, and files ticked for removal.

Qt-free. A choice is one of the options of its transcription (`options.py`); it waits in the
draft until Apply. "Transcribe all shown" and "Extract all shown" choose for every transcription
the embedded-subtitle view shows (narrowed by the filters, so per folder or per language): the
backend's suggestion (`ReviewFile.suggestions`: the pre-selected track, the text subtitle tagged
with the language), where it is one of the options. Choices can only be made while nothing holds
the job (a check, a save, its upload): what is shown is then the latest review. Ticks for
"Remove from job" are per file. Each edit tells the tables which rows it changed (`EditSignal`),
so they repaint only those.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from transcribe_ui.batches.classes import ExtractChoice, ReviewChoice, TranscribeChoice
from transcribe_ui.batches.responses import JobView, ReviewFile, ReviewSuggestion
from transcribe_ui.gui.review.draft import ChoiceDraft, ChoiceKey, choice_key
from transcribe_ui.gui.review.index import IndexCache
from transcribe_ui.gui.review.loading import ReviewLoader
from transcribe_ui.gui.review.options import current_choice, job_options

# Chooses for a transcription from its file, its job, the choice it shows and the stored one (None: no change).
type Suggested = Callable[[ReviewFile, JobView, ReviewChoice | None, ReviewChoice | None], ReviewChoice | None]


@dataclass(frozen=True)
class EditChange:
    """What an edit changed: the transcriptions whose choice changed and the files whose tick changed."""

    keys: frozenset[ChoiceKey] = frozenset()
    files: frozenset[str] = frozenset()


# Told what an edit changed.
type EditListener = Callable[[EditChange], None]
# Says whether the choices can change now.
type Check = Callable[[], bool]


class EditSignal:
    """Tells the tables which rows an edit changed, then the page that its display data changed."""

    def __init__(self, after: Callable[[], None]) -> None:
        """Call `after` following every edit."""
        self._listeners: list[EditListener] = []
        self._after = after

    def __call__(self, change: EditChange) -> None:
        """Tell the listeners what changed, then the page."""
        for listener in list(self._listeners):
            listener(change)
        self._after()

    def subscribe(self, listener: EditListener) -> None:
        """Tell `listener` what each edit changes."""
        self._listeners.append(listener)


def _job(review_file: ReviewFile, language: str) -> JobView | None:
    return next((job for job in review_file.jobs if job.language == language), None)


def _suggestion(review_file: ReviewFile, language: str) -> ReviewSuggestion | None:
    return next((shown for shown in review_file.suggestions if shown.language == language), None)


def _fits(review_file: ReviewFile, choice: ReviewChoice) -> bool:
    return choice in [option.choice for option in job_options(review_file, choice.language)]


class TickSet:
    """The files ticked for "Remove from job", in the order ticked."""

    def __init__(self) -> None:
        """Start without ticks."""
        self._ticked: dict[str, None] = {}

    def __contains__(self, item_id: object) -> bool:
        """Return whether a file is ticked."""
        return item_id in self._ticked

    def mark(self, item_id: str, *, ticked: bool) -> None:
        """Tick or untick a file."""
        if ticked:
            self._ticked[item_id] = None
        else:
            self._ticked.pop(item_id, None)

    def among(self, shown: Mapping[str, object]) -> tuple[str, ...]:
        """Return the ticked files `shown` lists, in the order ticked."""
        return tuple(item_id for item_id in self._ticked if item_id in shown)

    def keep_only(self, shown: Mapping[str, object]) -> None:
        """Drop the ticks of files `shown` does not list (another job: empty)."""
        self._ticked = dict.fromkeys(self.among(shown))


class ChoiceEditor:
    """The choices not applied yet and the files ticked for removal."""

    def __init__(self, loader: ReviewLoader, editable: Check, notify: Callable[[], None]) -> None:
        """Edit the review `loader` shows while `editable` holds; `notify` follows every edit."""
        # The rows of the review shown.
        self.views = IndexCache(loader)
        # Tells the tables which rows an edit changed.
        self.edits = EditSignal(notify)
        self.draft = ChoiceDraft()
        self.ticks = TickSet()
        self._editable = editable

    def choose(self, choice: ReviewChoice) -> None:
        """Choose for a transcription (ignored when it is not one of its options, or while the job is held)."""
        review_file = self.views.current().files.get(choice.item_id)
        job = None if review_file is None else _job(review_file, choice.language)
        if review_file is None or job is None or not self._editable():
            return
        if not _fits(review_file, choice):
            return
        self.draft.choose(choice, current_choice(choice.item_id, job))
        self.edits(EditChange(keys=frozenset((choice_key(choice),))))

    def transcribe_shown(self) -> None:
        """Choose transcribing (from the suggested track) for every transcription of the embedded-subtitle view."""
        self._choose_shown(_transcribe_suggestion)

    def extract_shown(self) -> None:
        """Choose extracting the suggested text subtitle for every transcription of the embedded-subtitle view."""
        self._choose_shown(_extract_suggestion)

    def discard(self) -> None:
        """Drop every choice not applied yet."""
        changed = frozenset(self.draft.keys())
        self.draft.clear()
        self.edits(EditChange(keys=changed))

    def tick(self, item_id: str, *, ticked: bool) -> None:
        """Tick or untick a file for "Remove from job"."""
        self.ticks.mark(item_id, ticked=ticked)
        self.edits(EditChange(files=frozenset((item_id,))))

    def ticked(self) -> tuple[str, ...]:
        """Return the files ticked for removal that the review still shows, in the order ticked."""
        return self.ticks.among(self.views.current().files)

    def prune(self) -> None:
        """Drop the choices and ticks of what the latest review no longer lists."""
        index = self.views.current()
        self.draft.keep_only(index.keys)
        self.ticks.keep_only(index.files)

    def reset(self) -> None:
        """Drop every choice and tick (another job)."""
        self.draft.clear()
        self.ticks.keep_only({})

    def _choose_shown(self, suggested: Suggested) -> None:
        if not self._editable():
            return
        changed: set[ChoiceKey] = set()
        for review_file in self.views.current().files.values():
            if review_file.subtitle_streams:
                changed.update(_choose_file(self.draft, review_file, suggested))
        self.edits(EditChange(keys=frozenset(changed)))


def _choose_file(draft: ChoiceDraft, review_file: ReviewFile, suggested: Suggested) -> list[ChoiceKey]:
    """Choose the suggestion for each job of a file; return the transcriptions chosen for."""
    chosen = []
    for job in review_file.jobs:
        key = (review_file.item_id, job.language)
        stored = current_choice(review_file.item_id, job)
        choice = suggested(review_file, job, draft.shown(key, stored), stored)
        if choice is not None and _fits(review_file, choice):
            draft.choose(choice, stored)
            chosen.append(key)
    return chosen


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
