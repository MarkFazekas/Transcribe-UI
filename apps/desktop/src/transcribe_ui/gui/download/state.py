"""What the download dialog shows now, from its view-model's state (architecture §3.1 "GUI" "Download").

Qt-free, so the tests read exactly what the widgets show. The plan's rows give way to the hint
while a choice is missing; Download is enabled only for a plan of the current choices without
errors; the download's status, Cancel, Resume and Details follow its activity entry.
"""

from dataclasses import dataclass

from transcribe_ui.batches.responses import BatchStage
from transcribe_ui.gui.activity import ActivityEntry
from transcribe_ui.gui.download.constants import CELL_SEPARATOR
from transcribe_ui.gui.download.display import FailureRow, PlanRow, failure_rows, level_text, outcome_summary
from transcribe_ui.gui.download.form import form_hint
from transcribe_ui.gui.download.viewmodel import DownloadModel, can_download
from transcribe_ui.gui.feedback import ErrorView
from transcribe_ui.i18n.catalog import tr


@dataclass(frozen=True)
class PlanData:
    """The plan part: what is missing, the error of a load, the plan's errors, its summary and rows."""

    # What the user still has to choose, or None.
    hint: str | None
    # The error of the last load (with a Retry that loads again), or None.
    error: ErrorView | None
    # "Updating the plan…" while the plan of the current choices is on its way, else "".
    loading: str
    errors: tuple[str, ...]
    summary: str
    rows: tuple[PlanRow, ...]


@dataclass(frozen=True)
class RunData:
    """The download part: its status and the buttons that apply, and what the last download did."""

    # The download's status and the file it is at ("" before a download).
    status: str
    can_cancel: bool
    can_resume: bool
    has_details: bool
    outcome: str
    failures: tuple[FailureRow, ...]


@dataclass(frozen=True)
class DialogData:
    """Everything the dialog shows besides the choices themselves."""

    stage: BatchStage | None
    # The folder levels to choose from: level and its text.
    levels: tuple[tuple[int, str], ...]
    plan: PlanData
    can_download: bool
    run: RunData
    # Where the custom plan was exported to, and the imported plan file ("" for none).
    exported: str
    imported: str
    # The error of the last export (with a Retry that exports again), or None.
    export_error: ErrorView | None


def _plan_data(model: DownloadModel) -> PlanData:
    loaded = model.loaded
    hint = form_hint(model.form, model.plan_path)
    # While a choice is missing the plan (if any) is not the one a download would carry out.
    view = loaded.view if hint is None else None
    return PlanData(
        hint=hint,
        error=loaded.error,
        loading=tr("Updating the plan…") if loaded.loading else "",
        errors=() if view is None else view.errors,
        summary="" if view is None else view.summary,
        rows=() if view is None else view.rows,
    )


def _status(entry: ActivityEntry) -> str:
    # The file being placed only while the download runs.
    path = entry.path if entry.can_cancel else ""
    return CELL_SEPARATOR.join(part for part in (entry.status, path) if part)


def _run_data(model: DownloadModel) -> RunData:
    entry = model.run.entry
    outcome = model.run.outcome
    return RunData(
        status="" if entry is None else _status(entry),
        can_cancel=entry is not None and entry.can_cancel,
        can_resume=entry is not None and entry.can_resume,
        has_details=entry is not None and entry.error is not None,
        outcome="" if outcome is None else outcome_summary(outcome),
        failures=() if outcome is None else failure_rows(outcome),
    )


def _file_text(template: str, path: str | None) -> str:
    return "" if path is None else template.format(path=path)


def dialog_data(model: DownloadModel) -> DialogData:
    """Return what the dialog shows for the model's state now."""
    loaded = model.loaded
    summary = loaded.summary
    exported = tr("The plan was saved to {path}. Fill in output_folder and output_file_name, then import it.")
    return DialogData(
        stage=None if summary is None else summary.stage,
        levels=tuple((level.level, level_text(level)) for level in loaded.levels),
        plan=_plan_data(model),
        can_download=can_download(model),
        run=_run_data(model),
        exported=_file_text(exported, model.export.path),
        imported=_file_text(tr("Imported plan: {path}"), model.plan_path),
        export_error=model.export.error,
    )
