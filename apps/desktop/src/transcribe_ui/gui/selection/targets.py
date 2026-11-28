"""The languages a new job transcribes into: the job's, and other ones for single files (§3.3).

Qt-free. The job's languages apply to every selected file; a file can have its own instead
(`item_languages`). A file's own languages are kept while it is unticked, so ticking it again
brings them back, but a request names only the files the latest preview selected (the
backend refuses an entry for a file outside the selection).
"""

from collections.abc import Callable, Iterable

from transcribe_ui.batches.classes import ItemLanguages


class TargetModel:
    """The job's target languages and each file's own ones."""

    def __init__(self, notify: Callable[[], None]) -> None:
        """Start without languages; `notify` tells the page about a change."""
        self._notify = notify
        self.languages: tuple[str, ...] = ()
        # Counts the changes, so the tree's language column repaints only then.
        self.version = 0
        # Own languages per file path, in the order they were set.
        self._own: dict[str, tuple[str, ...]] = {}

    def use_languages(self, languages: tuple[str, ...]) -> None:
        """Set the job's languages."""
        self.languages = languages
        self._changed()

    def use_own(self, path: str, languages: tuple[str, ...] | None) -> None:
        """Give a file its own languages; None (or none) gives it the job's again."""
        self._own.pop(path, None)
        if languages:
            self._own[path] = languages
        self._changed()

    def own(self, path: str) -> tuple[str, ...] | None:
        """Return a file's own languages, or None when it takes the job's."""
        return self._own.get(path)

    def of(self, path: str) -> tuple[str, ...]:
        """Return the languages a file is transcribed into."""
        return self._own.get(path, self.languages)

    def entries(self, selected: frozenset[str]) -> tuple[ItemLanguages, ...]:
        """Return the request's `item_languages`: the own languages of the selected files."""
        entries = []
        for path, languages in self._own.items():
            if path in selected:
                entries.append(ItemLanguages(path=path, languages=languages))
        return tuple(entries)

    def reset(self, languages: tuple[str, ...], own: Iterable[ItemLanguages] = ()) -> None:
        """Start again with these languages (a reopened job's, or the ones kept for a new job)."""
        self.languages = languages
        self._own = {entry.path: entry.languages for entry in own}
        self._changed()

    def _changed(self) -> None:
        self.version += 1
        self._notify()
