"""The filters of the New job page and what they match (architecture §3.2).

Qt-free. The filter bar's values become the request's `SelectionFilter` (its own validation
refuses a bad regular expression, so the GUI checks nothing itself). A folder ticked while
filters are active selects only the matching files below it: the operation carries the
filters. To show which files match, a filter change sends a preview of one `include` of the
scan root with those filters (latest wins); the tree dims what it does not list. Without an
active filter everything matches and nothing is sent.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from pydantic import ValidationError

from transcribe_ui.api.classes import ErrorResponse, Response
from transcribe_ui.discovery.classes import (
    NameFilter,
    NameMatch,
    PreviewSelectionResponse,
    SelectionFilter,
    SelectionOperation,
    SubtitleFilter,
    SubtitleRule,
)
from transcribe_ui.discovery.constants import REGEX_MAX_LENGTH
from transcribe_ui.gui.executor import Executor, LatestChannel
from transcribe_ui.gui.feedback import ErrorView, error_view
from transcribe_ui.gui.selection.constants import MATCH_KEY, KindChoice
from transcribe_ui.gui.selection.marks import Marks
from transcribe_ui.gui.selection.tree import Scanned
from transcribe_ui.i18n.catalog import tr


@dataclass(frozen=True)
class FilterInput:
    """What the filter bar holds."""

    media_kind: KindChoice = "any"
    # Subtitle languages of the subtitle filter; none: no subtitle filter.
    subtitle_languages: tuple[str, ...] = ()
    subtitle_rule: SubtitleRule = "any"
    name_match: NameMatch = "contains"
    # Text of the name filter; empty: no name filter.
    name_text: str = ""

    def active(self) -> bool:
        """Return whether any filter is set."""
        narrowed = bool(self.subtitle_languages or self.name_text)
        return narrowed or self.media_kind != "any"


def selection_filter(filters: FilterInput) -> SelectionFilter:
    """Return the request's filter of the bar's values.

    Raises:
        ValidationError: If the request refuses them (a bad regular expression).
    """
    subtitles = None
    if filters.subtitle_languages:
        subtitles = SubtitleFilter(languages=filters.subtitle_languages, rule=filters.subtitle_rule)
    name = None
    if filters.name_text:
        name = NameFilter(match=filters.name_match, text=filters.name_text)
    return SelectionFilter(media_kind=filters.media_kind, subtitles=subtitles, name=name)


class FilterModel:
    """The active filters, whether they can be used, and the files and folders they match."""

    def __init__(self, executor: Executor, notify: Callable[[], None]) -> None:
        """Send the match previews through `executor`; `notify` tells the page about a change."""
        self._channel = LatestChannel(executor)
        self._notify = notify
        self.picked = FilterInput()
        # What the active filters match (None: every file, no filter is active).
        self.matches: Marks | None = None
        # Why the filters cannot be used (a bad regular expression), or None.
        self.problem: str | None = None
        # The error of the last match preview, or None.
        self.error: ErrorView | None = None
        # Counts the changes of what is dimmed, so the tree repaints only then.
        self.version = 0
        self._generation = 0

    def current(self) -> SelectionFilter | None:
        """Return the active filters as the request's filter, or None while they cannot be used."""
        try:
            return selection_filter(self.picked)
        except ValidationError:
            return None

    def change(self, picked: FilterInput, scanned: Scanned | None) -> None:
        """Take the bar's values, then show what they match on the scan."""
        self.picked = picked
        self.refresh(scanned)

    def refresh(self, scanned: Scanned | None) -> None:
        """Send the match preview of the active filters on the scan (none without an active filter)."""
        chosen = self.current()
        self.problem = _problem_text() if chosen is None else None
        self.error = None
        # An answer to an earlier refresh no longer counts.
        self._generation += 1
        if scanned is None or chosen is None or not self.picked.active():
            self._show(None)
            return
        operation = SelectionOperation(operation="include", path=scanned.index.tree.root, filters=chosen)
        matched = partial(self._matched, self._generation)
        self._channel.send(MATCH_KEY, scanned.preview_request((operation,)), matched)

    def matching(self, path: str, *, folder: bool) -> bool:
        """Return whether a file matches the active filters, or a folder has a matching file below it."""
        matches = self.matches
        if matches is None:
            return True
        return path in matches.folders if folder else path in matches.files

    def _matched(self, generation: int, response: Response) -> None:
        if generation != self._generation:
            return
        if isinstance(response, PreviewSelectionResponse):
            self._show(Marks.of(response))
        elif isinstance(response, ErrorResponse):
            self.error = error_view(response)
            self._notify()

    def _show(self, matches: Marks | None) -> None:
        self.matches = matches
        self.version += 1
        self._notify()


def _problem_text() -> str:
    text = tr("The name filter is not a valid regular expression (at most {count} characters).")
    return text.format(count=REGEX_MAX_LENGTH)
