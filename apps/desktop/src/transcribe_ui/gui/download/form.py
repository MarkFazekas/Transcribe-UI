"""The download dialog's choices and the requests they make (architecture §3.1 "GUI" "Download").

Qt-free. The form holds the widget state as data; these functions turn it into the backend's
requests. The placement settings only pre-fill the form (`form_from_settings`); each request is complete
on its own, so the CLI could replay it. Which choices a request needs is the request model's
rule: a form it refuses makes no request, and `form_hint` tells the user what is missing.
"""

from dataclasses import dataclass, field, replace
from typing import TypedDict

from pydantic import ValidationError

from transcribe_ui.api.classes import GetSettingsResponse, PlacementDefaults, Response
from transcribe_ui.batches.responses import GetBatchResponse
from transcribe_ui.gui.download.constants import (
    CUSTOM,
    FLAT_WITH_FOLDERS,
    FOLDER_COUNT_MODE,
    FOLDER_LEVELS_MODE,
    MAX_FOLDER_COUNT,
    MIRROR,
    NEXT_TO_MEDIA,
    FolderMode,
    StrategyChoice,
)
from transcribe_ui.i18n.catalog import tr
from transcribe_ui.placement.classes import (
    CustomPlacementOptions,
    ImportCustomPlanRequest,
    PlacementOptions,
    PlanDownloadRequest,
    RunDownloadRequest,
)
from transcribe_ui.placement.constants import DEFAULT_FOLDER_COUNT

# The request that shows what a download would do: a plan, or the check of an imported custom plan.
type PlanLoad = PlanDownloadRequest | ImportCustomPlanRequest
# The request that downloads: with a strategy, or as the imported custom plan says.
type DownloadLoad = RunDownloadRequest | ImportCustomPlanRequest


@dataclass(frozen=True)
class DownloadForm:
    """The dialog's choices: strategy, output folder, the folders kept in the names, and the outcome options."""

    strategy: StrategyChoice = NEXT_TO_MEDIA
    # As typed or chosen; "" while none is given.
    output_folder: str = ""
    folder_mode: FolderMode = FOLDER_COUNT_MODE
    folder_count: int = DEFAULT_FOLDER_COUNT
    folder_levels: frozenset[int] = field(default_factory=frozenset)
    overwrite: bool = False
    include_empty: bool = False


class FormChanges(TypedDict, total=False):
    """Choices changed in the dialog (any of the form's fields)."""

    strategy: StrategyChoice
    output_folder: str
    folder_mode: FolderMode
    folder_count: int
    folder_levels: frozenset[int]
    overwrite: bool
    include_empty: bool


def form_from_settings(defaults: PlacementDefaults) -> DownloadForm:
    """Return the form pre-filled from the settings' placement defaults."""
    return DownloadForm(
        strategy=defaults.strategy,
        output_folder=defaults.output_folder or "",
        # The dialog offers at most `MAX_FOLDER_COUNT` (a larger count keeps every folder anyway).
        folder_count=min(defaults.folder_count, MAX_FOLDER_COUNT),
        overwrite=defaults.overwrite,
    )


def first_choices(settings: Response, batch: Response) -> DownloadForm:
    """Return the first opening's choices from the answers of `get_settings` and `get_batch` (either may have failed).

    The settings' placement defaults (the request's defaults without them), with `mirror` for a job
    uploaded from another computer: its originals are there, so next to the media is refused for
    it (`no_local_sources`), and `mirror` keeps its original folders below the output folder.
    """
    form = DownloadForm()
    if isinstance(settings, GetSettingsResponse):
        form = form_from_settings(settings.settings.placement)
    if isinstance(batch, GetBatchResponse) and batch.batch.summary.adopted:
        return replace(form, strategy=MIRROR)
    return form


def _levels_chosen(form: DownloadForm) -> bool:
    return form.strategy == FLAT_WITH_FOLDERS and form.folder_mode == FOLDER_LEVELS_MODE


def placement_options(form: DownloadForm) -> PlacementOptions:
    """Return the placement options of a strategy (not the custom plan).

    Raises:
        ValidationError: The options do not fit the strategy (an output folder is missing).
    """
    counted = form.strategy == FLAT_WITH_FOLDERS and form.folder_mode == FOLDER_COUNT_MODE
    levels = tuple(sorted(form.folder_levels)) if _levels_chosen(form) else ()
    return PlacementOptions.model_validate(
        {
            "strategy": form.strategy,
            # next_to_media ignores an output folder; the request then carries none.
            "output_folder": None if form.strategy == NEXT_TO_MEDIA else form.output_folder or None,
            "folder_count": form.folder_count if counted else DEFAULT_FOLDER_COUNT,
            "folder_levels": levels,
            "overwrite": form.overwrite,
            "include_empty": form.include_empty,
        },
    )


def _custom_options(form: DownloadForm) -> CustomPlacementOptions:
    """Return where an imported custom plan's folders start, and the outcome options.

    Raises:
        ValidationError: The output folder is missing or invalid.
    """
    return CustomPlacementOptions.model_validate(
        {"output_folder": form.output_folder, "overwrite": form.overwrite, "include_empty": form.include_empty},
    )


def _import_request(batch_id: str, form: DownloadForm, plan_path: str, *, check_only: bool) -> ImportCustomPlanRequest:
    """Return the import of the custom plan file.

    Raises:
        ValidationError: The output folder or the plan path is not valid.
    """
    return ImportCustomPlanRequest(
        action="import_custom_plan",
        batch_id=batch_id,
        plan_path=plan_path,
        placement=_custom_options(form),
        check_only=check_only,
    )


def _plan_load(batch_id: str, form: DownloadForm, plan_path: str | None) -> PlanLoad | None:
    if form.strategy != CUSTOM:
        return PlanDownloadRequest(action="plan_download", batch_id=batch_id, placement=placement_options(form))
    if plan_path is None:
        return None
    return _import_request(batch_id, form, plan_path, check_only=True)


def plan_request(batch_id: str, form: DownloadForm, plan_path: str | None) -> PlanLoad | None:
    """Return the request that shows what the download would do, or None while the form cannot make one.

    With levels chosen but none ticked, the plan is still asked for (it lists the levels to choose
    from); `form_hint` then says to choose one, and nothing can be downloaded.
    """
    try:
        return _plan_load(batch_id, form, plan_path)
    except ValidationError:
        return None


def form_hint(form: DownloadForm, plan_path: str | None) -> str | None:
    """Return what the user still has to choose before the download can start, or None."""
    if form.strategy != NEXT_TO_MEDIA and not form.output_folder:
        return tr("Choose the output folder.")
    if form.strategy == CUSTOM and plan_path is None:
        return tr("Export the plan, fill in each output folder and file name, then import it.")
    if _levels_chosen(form) and not form.folder_levels:
        return tr("Choose at least one folder level.")
    return None


def download_request(batch_id: str, form: DownloadForm, plan_path: str | None) -> DownloadLoad | None:
    """Return the request that downloads as the form says, or None while something is missing."""
    if form_hint(form, plan_path) is not None:
        return None
    if plan_request(batch_id, form, plan_path) is None:
        return None
    if form.strategy == CUSTOM and plan_path is not None:
        return _import_request(batch_id, form, plan_path, check_only=False)
    return RunDownloadRequest(action="run_download", batch_id=batch_id, placement=placement_options(form))
