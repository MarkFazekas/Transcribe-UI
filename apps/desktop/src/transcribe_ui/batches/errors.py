"""Domain errors of the batches and the review step; the backend turns them into error responses (§6)."""

from collections.abc import Iterable
from typing import TYPE_CHECKING

from transcribe_ui.api.errors import DomainError
from transcribe_ui.batches.constants import (
    ERROR_CHOICE_INVALID,
    ERROR_ITEM_NOT_FOUND,
    ERROR_LAST_ITEM,
    ERROR_NOTHING_SELECTED,
    ERROR_PATH_NOT_SELECTED,
    ERROR_WRONG_PHASE,
)

if TYPE_CHECKING:
    from pydantic import JsonValue


class WrongPhaseError(DomainError):
    """The request is not possible in the batch's phase; nothing was changed."""

    def __init__(self, batch_id: str, phase: str, allowed: Iterable[str]) -> None:
        """Name the batch, its phase and the phases the request needs."""
        phases: list[JsonValue] = []
        phases.extend(sorted(allowed))
        details: dict[str, JsonValue] = {"batch_id": batch_id, "phase": phase, "allowed": phases}
        super().__init__(ERROR_WRONG_PHASE, "This is not possible in the batch's current phase.", details)


class NothingSelectedError(DomainError):
    """The selection selects no media file, so there is no batch to make."""

    def __init__(self) -> None:
        """Use the fixed `nothing_selected` code and message."""
        super().__init__(ERROR_NOTHING_SELECTED, "The selection contains no media file.")


class PathNotSelectedError(DomainError):
    """An `item_languages` entry names a file the selection does not select."""

    def __init__(self, index: int) -> None:
        """Name the entry by its index (details are not path-normalised; the request holds the path)."""
        super().__init__(
            ERROR_PATH_NOT_SELECTED,
            "item_languages names a file that is not in the selection.",
            {"index": index},
        )


class ItemNotFoundError(DomainError):
    """An item id of the request is not an item of the batch."""

    def __init__(self, batch_id: str, item_ids: Iterable[str]) -> None:
        """Name the batch and the unknown item ids."""
        unknown: list[JsonValue] = []
        unknown.extend(sorted(item_ids))
        super().__init__(
            ERROR_ITEM_NOT_FOUND,
            "The batch has no item with this id.",
            {"batch_id": batch_id, "item_ids": unknown},
        )


class LastItemError(DomainError):
    """Excluding these items would leave the batch empty; delete or clean up the batch instead."""

    def __init__(self, batch_id: str) -> None:
        """Name the batch."""
        super().__init__(
            ERROR_LAST_ITEM,
            "A batch cannot lose its last item; delete the batch or clean it up from the cloud instead.",
            {"batch_id": batch_id},
        )


class ChoiceInvalidError(DomainError):
    """A review choice does not fit its item; no choice of the request was applied."""

    def __init__(self, index: int, reason: str, message: str) -> None:
        """Name the choice by its index in the request, and why it does not fit."""
        super().__init__(ERROR_CHOICE_INVALID, message, {"choice": index, "reason": reason})
