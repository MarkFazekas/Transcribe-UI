"""Errors of the run-activity check."""

from collections.abc import Sequence
from typing import Literal

from pydantic import JsonValue

from transcribe_ui.api.errors import DomainError
from transcribe_ui.runpod.constants import ERROR_CLOUD_UNREACHABLE
from transcribe_ui.runs.classes import ActivitySource
from transcribe_ui.runs.constants import ERROR_RUN_ACTIVE

# Which part of the cloud could not be read.
type CloudPart = Literal["runpod", "r2"]


class ActivityUnknownError(DomainError):
    """The Runpod lists or R2 could not be read, so a run may be active: the check fails closed."""

    def __init__(self, part: CloudPart, cause: str) -> None:
        """Name the part and the cause (an error code, never request data) in the details."""
        super().__init__(
            ERROR_CLOUD_UNREACHABLE,
            "Whether a worker run is active cannot be checked, so nothing was started or published; try again later.",
            {"part": part, "cause": cause},
        )


class RunActiveError(DomainError):
    """A worker run is active, so the request would change what it may be working on; nothing was changed."""

    def __init__(self, sources: Sequence[ActivitySource]) -> None:
        """Name the active runs and every sign of them in the details."""
        named = {source.run_id for source in sources}
        run_ids: list[JsonValue] = []
        run_ids.extend(sorted(run_id for run_id in named if run_id is not None))
        signs: list[JsonValue] = []
        signs.extend(_sign(source) for source in sources)
        super().__init__(
            ERROR_RUN_ACTIVE,
            "A worker run is active; nothing was changed. Try again once it has ended.",
            {"run_ids": run_ids, "sources": signs},
        )


def _sign(source: ActivitySource) -> JsonValue:
    sign: dict[str, JsonValue] = {"kind": source.kind, "name": source.name}
    sign.update({"status": source.status, "run_id": source.run_id})
    return sign
