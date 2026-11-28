"""What the download dialog loads, and the delay of its plan loads (architecture §3.1 "GUI" "Download").

Qt-free. Each answer of a load becomes the dialog's `Loaded` state; a plan's rows, errors and
summary are built once, when it arrives.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from transcribe_ui.api.classes import ErrorResponse, Response
from transcribe_ui.batches.responses import BatchSummary, GetBatchResponse
from transcribe_ui.gui.download.display import PlanView, plan_view
from transcribe_ui.gui.feedback import ErrorView, error_view
from transcribe_ui.placement.classes import (
    FolderLevel,
    ImportCustomPlanResponse,
    PlacementPlan,
    PlanDownloadResponse,
    RunDownloadResponse,
)


class Deferral(Protocol):
    """Calls a function once after `PLAN_DELAY_MS`, unless asked again or cancelled first (a Qt timer; tests fire it)."""

    def call_later(self, callback: Callable[[], None]) -> None:
        """Call `callback` after the delay; a pending call is replaced (its delay starts again)."""
        ...

    def cancel(self) -> None:
        """Drop the pending call, if any."""
        ...


@dataclass(frozen=True)
class Loaded:
    """What the dialog loaded: the batch, its folder levels and the plan of the current choices."""

    summary: BatchSummary | None = None
    levels: tuple[FolderLevel, ...] = ()
    # The plan of the current choices (or of the imported custom plan); None before one arrived.
    plan: PlacementPlan | None = None
    # How the plan is shown (built once per plan); None without a plan.
    view: PlanView | None = None
    # Whether a plan for the current choices is on its way (the plan shown may be the previous one).
    loading: bool = False
    # The error of the last load, shown in the dialog with Retry; None after a load that succeeded.
    error: ErrorView | None = None


def failed_some(response: Response) -> bool:
    """Return whether a download's answer lists jobs it could not place."""
    if not isinstance(response, RunDownloadResponse | ImportCustomPlanResponse) or response.download is None:
        return False
    return bool(response.download.failures)


def _after_error(loaded: Loaded, response: ErrorResponse) -> Loaded:
    error = error_view(response)
    if response.action == "get_batch":
        return replace(loaded, error=error)
    # The plan of the current choices could not be made: none is shown.
    return replace(loaded, plan=None, view=None, loading=False, error=error)


def _planned(summary: BatchSummary, levels: tuple[FolderLevel, ...], plan: PlacementPlan) -> Loaded:
    return Loaded(summary=summary, levels=levels, plan=plan, view=plan_view(plan))


def after_load(loaded: Loaded, response: Response) -> Loaded:
    """Return what the dialog has loaded once a load answered."""
    if isinstance(response, GetBatchResponse):
        return replace(loaded, summary=response.batch.summary)
    if isinstance(response, PlanDownloadResponse):
        return _planned(response.batch, response.folder_levels, response.plan)
    if isinstance(response, ImportCustomPlanResponse):
        return _planned(response.batch, loaded.levels, response.plan)
    if isinstance(response, ErrorResponse):
        return _after_error(loaded, response)
    return loaded
