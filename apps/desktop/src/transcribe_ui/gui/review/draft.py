"""The review choices made on the page and not applied yet (architecture §3.1 "GUI" "Review and upload").

Qt-free. A choice is kept per transcription (file and language) in the order the user made it,
until Apply sends it (`resolve_review`) or Discard drops it. A choice equal to the stored one is
no change. Choices stay while filters hide their rows, so a choice made in one folder is applied
with the others.
"""

from collections.abc import Iterable

from transcribe_ui.batches.classes import ReviewChoice

# A transcription of a file in review: its item id and its language.
type ChoiceKey = tuple[str, str]


def choice_key(choice: ReviewChoice) -> ChoiceKey:
    """Return the transcription a choice is about."""
    return choice.item_id, choice.language


class ChoiceDraft:
    """The choices not applied yet, by transcription, in the order made."""

    def __init__(self) -> None:
        """Start without choices."""
        self._choices: dict[ChoiceKey, ReviewChoice] = {}

    def shown(self, key: ChoiceKey, stored: ReviewChoice | None) -> ReviewChoice | None:
        """Return the choice a transcription shows: the one made here, else the stored one."""
        return self._choices.get(key, stored)

    def changed(self, key: ChoiceKey) -> bool:
        """Return whether a choice made here waits for Apply."""
        return key in self._choices

    def choose(self, choice: ReviewChoice, stored: ReviewChoice | None) -> None:
        """Keep a choice (choosing the stored one again drops the change)."""
        key = choice_key(choice)
        self._choices.pop(key, None)
        if choice != stored:
            self._choices[key] = choice

    def pending(self) -> tuple[ReviewChoice, ...]:
        """Return the choices waiting for Apply, in the order made."""
        return tuple(self._choices.values())

    def forget(self, sent: Iterable[ReviewChoice]) -> None:
        """Drop the choices Apply stored (a choice changed again since it was sent stays)."""
        for choice in sent:
            key = choice_key(choice)
            if self._choices.get(key) == choice:
                self._choices.pop(key)

    def drop_items(self, item_ids: Iterable[str]) -> None:
        """Drop the choices of files that left the job."""
        gone = frozenset(item_ids)
        for key in list(self._choices):
            if key[0] in gone:
                self._choices.pop(key)

    def clear(self) -> None:
        """Drop every choice (Discard, or another job)."""
        self._choices = {}
