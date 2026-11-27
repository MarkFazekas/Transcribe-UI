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
from transcribe_ui.placement import custom
from transcribe_ui.placement.classes import (
    ExportCustomPlanRequest,
    ExportCustomPlanResponse,
    ImportCustomPlanRequest,
    ImportCustomPlanResponse,
    PlacementPlan,
    PlanDownloadRequest,
    PlanDownloadResponse,
    RunDownloadRequest,
    RunDownloadResponse,
)
from transcribe_ui.placement.download import Carrying, carry_out
from transcribe_ui.placement.plan_files import read_plan, write_plan
from transcribe_ui.placement.planner import plan_placement
from transcribe_ui.placement.probe import OsPlacementProbe
from transcribe_ui.placement.sources import DownloadSource, load_source


def plan_download(request: PlanDownloadRequest, context: RequestContext) -> PlanDownloadResponse:
    """Return the plan of placing the batch's subtitles with a strategy; nothing is written.

    Raises:
        BatchNotFoundError: There is no such batch.
        WrongPhaseError: The batch's subtitles cannot be downloaded in its phase.
    """
    source = load_source(context, request.batch_id)
    jobs = source.placement_jobs()
    rules = context.ports.path_rules
    plan = plan_placement(jobs, source.scan_root, request.placement, rules, OsPlacementProbe())
    return PlanDownloadResponse(batch=source.summary, plan=plan)


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
        rules = context.ports.path_rules
        plan = plan_placement(source.placement_jobs(), source.scan_root, placement, rules, OsPlacementProbe())
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
    plan = custom.export_custom_plan(source.placement_jobs(), source.scan_root, context.ports.path_rules)
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
        DomainError: See `placement.download.carry_out`; what was placed is recorded.
    """
    batch_id = request.batch_id
    if request.check_only:
        source, plan = _imported(context, request)
        return ImportCustomPlanResponse(batch=source.summary, plan=plan, download=None)
    with context.ports.state.locks.batch(batch_id):
        source, plan = _imported(context, request)
        carrying = Carrying(request.action, next_to_media=False, confirm_overwrite=request.confirm_overwrite)
        download = carry_out(context, source, plan, carrying)
        if isinstance(download, NeedsConfirmation):
            return download
        return ImportCustomPlanResponse(batch=_summary(context, batch_id), plan=plan, download=download)


def _imported(context: RequestContext, request: ImportCustomPlanRequest) -> tuple[DownloadSource, PlacementPlan]:
    source = load_source(context, request.batch_id)
    document = read_plan(Path(request.plan_path))
    jobs = source.placement_jobs()
    rules = context.ports.path_rules
    plan = custom.import_custom_plan(document, jobs, request.placement, rules, OsPlacementProbe())
    return source, plan


def _summary(context: RequestContext, batch_id: str) -> BatchSummary:
    with context.ports.state.session() as session:
        return summary_of(session, load_batch(session, batch_id), stored_items(session, batch_id))
