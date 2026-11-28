"""Handlers of the download and custom plan requests (architecture §3.5, plan m5 §1 "Download").

`plan_download`, `export_custom_plan` and `import_custom_plan` with `check_only` only read (no
lock; the export may write the plan file). `run_download` and `import_custom_plan` hold the
batch's lock (never waited for) and carry the validated plan out (`placement/download.py`). The target OS's rules are this computer's
(`Ports.path_rules`), and the planner asks the real file system (`OsPlacementProbe`).
"""

from pathlib import Path

from transcribe_ui.api.classes import NeedsConfirmation
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.phases import load_batch
from transcribe_ui.batches.records import stored_items, summary_of
from transcribe_ui.batches.responses import BatchSummary
from transcribe_ui.discovery.rules import PathRules
from transcribe_ui.placement import custom
from transcribe_ui.placement.classes import (
    ExportCustomPlanRequest,
    ExportCustomPlanResponse,
    ImportCustomPlanRequest,
    ImportCustomPlanResponse,
    PlacementOptions,
    PlacementPlan,
    PlanDownloadRequest,
    PlanDownloadResponse,
    RunDownloadRequest,
    RunDownloadResponse,
)
from transcribe_ui.placement.download import Carrying, carry_out
from transcribe_ui.placement.levels import folder_levels
from transcribe_ui.placement.plan_files import read_plan, write_plan
from transcribe_ui.placement.planner import plan_placement, refuse_next_to_media
from transcribe_ui.placement.probe import CancelCheckProbe, OsPlacementProbe, PlacementProbe
from transcribe_ui.placement.sources import DownloadSource, load_source


def plan_download(request: PlanDownloadRequest, context: RequestContext) -> PlanDownloadResponse:
    """Return the plan of placing the batch's subtitles with a strategy, and its folder levels; nothing is written.

    Raises:
        BatchNotFoundError: There is no such batch.
        WrongPhaseError: The batch's subtitles cannot be downloaded in its phase.
        CancelledError: Cancelled while planning (nothing is written either way).
    """
    source = load_source(context, request.batch_id)
    rules = context.ports.path_rules
    probe = CancelCheckProbe(OsPlacementProbe(), context.check_cancelled)
    plan = _plan(source, request.placement, rules, probe)
    levels = folder_levels(source.placement_jobs(), source.scan_root, source.names(rules))
    return PlanDownloadResponse(batch=source.summary, plan=plan, folder_levels=levels)


def run_download(request: RunDownloadRequest, context: RequestContext) -> RunDownloadResponse | NeedsConfirmation:
    """Place the batch's finished subtitles with a strategy, recording each job's outcome.

    Raises:
        BatchBusyError: Another request works on the batch.
        DownloadPlanInvalidError: The plan has errors (nothing was written).
        DomainError: See `placement.download.carry_out`; what was placed is recorded.
    """
    batch_id = request.batch_id
    placement = request.placement
    with context.ports.state.locks.batch(batch_id):
        source = load_source(context, batch_id)
        plan = _plan(source, placement, context.ports.path_rules, OsPlacementProbe())
        next_to_media = placement.strategy == "next_to_media"
        carrying = Carrying(request.action, next_to_media=next_to_media, confirm_overwrite=request.confirm_overwrite)
        download = carry_out(context, source, plan, carrying)
        if isinstance(download, NeedsConfirmation):
            return download
        return RunDownloadResponse(batch=_summary(context, batch_id), download=download)


def export_custom_plan(request: ExportCustomPlanRequest, context: RequestContext) -> ExportCustomPlanResponse:
    """Return the batch's custom plan (and write it to the file, if asked).

    Raises:
        BatchNotFoundError: There is no such batch.
        WrongPhaseError: The batch's subtitles cannot be downloaded in its phase.
        CustomPlanUnwritableError: The plan file could not be written.
    """
    source = load_source(context, request.batch_id)
    names = source.names(context.ports.path_rules)
    plan = custom.export_custom_plan(source.placement_jobs(), source.scan_root, names)
    if request.path is not None:
        write_plan(Path(request.path), plan)
    return ExportCustomPlanResponse(batch=source.summary, plan=plan, path=request.path)


def import_custom_plan(
    request: ImportCustomPlanRequest,
    context: RequestContext,
) -> ImportCustomPlanResponse | NeedsConfirmation:
    """Validate the edited custom plan file; unless only checking, place the subtitles as it says.

    Raises:
        BatchBusyError: Another request works on the batch.
        CustomPlanUnreadableError: The plan file could not be read.
        DownloadPlanInvalidError: The plan has errors and it was not only checked (nothing was written).
        CancelledError: Cancelled while only checking (nothing is written either way).
        DomainError: See `placement.download.carry_out`; what was placed is recorded.
    """
    batch_id = request.batch_id
    if request.check_only:
        checked = CancelCheckProbe(OsPlacementProbe(), context.check_cancelled)
        source, plan = _imported(context, request, checked)
        return ImportCustomPlanResponse(batch=source.summary, plan=plan, download=None)
    with context.ports.state.locks.batch(batch_id):
        source, plan = _imported(context, request, OsPlacementProbe())
        carrying = Carrying(request.action, next_to_media=False, confirm_overwrite=request.confirm_overwrite)
        download = carry_out(context, source, plan, carrying)
        if isinstance(download, NeedsConfirmation):
            return download
        return ImportCustomPlanResponse(batch=_summary(context, batch_id), plan=plan, download=download)


def _imported(
    context: RequestContext,
    request: ImportCustomPlanRequest,
    probe: PlacementProbe,
) -> tuple[DownloadSource, PlacementPlan]:
    source = load_source(context, request.batch_id)
    document = read_plan(Path(request.plan_path))
    names = source.names(context.ports.path_rules)
    plan = custom.import_custom_plan(document, source.placement_jobs(), request.placement, names, probe)
    return source, plan


def _summary(context: RequestContext, batch_id: str) -> BatchSummary:
    now = context.ports.clock.now()
    with context.ports.state.session() as session:
        members = stored_items(session, batch_id)
        return summary_of(session, load_batch(session, batch_id), members, now=now)


def _plan(source: DownloadSource, options: PlacementOptions, rules: PathRules, probe: PlacementProbe) -> PlacementPlan:
    """Return the plan of placing the batch's subtitles; another computer's batch is never placed next to its media."""
    if source.adopted and options.strategy == "next_to_media":
        return refuse_next_to_media()
    names = source.names(rules)
    return plan_placement(source.placement_jobs(), source.scan_root, options, names, probe)
