"""Display data of the download dialog (architecture §3.1 "GUI" "Download").

Qt-free. Everything shown comes from the backend's responses: the plan's entries with their
outcomes and rename reasons, its errors, the folder levels, the download's outcome and its
failures. Only presentation is decided here: texts, counting the outcomes for the summary line,
and splitting a path into its folder and name for the table.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePath

from transcribe_ui.gui.download.constants import CELL_SEPARATOR, LIST_SEPARATOR, SKIPPED_OUTCOMES, StrategyChoice
from transcribe_ui.i18n.catalog import ntr, tr
from transcribe_ui.i18n.formats import format_number
from transcribe_ui.i18n.texts import code_text
from transcribe_ui.placement.classes import (
    DownloadFailure,
    DownloadOutcome,
    FolderLevel,
    PlacementOutcome,
    PlacementPlan,
    PlanError,
    PlannedPlacement,
)


@dataclass(frozen=True)
class PlanRow:
    """One job of the plan: the media file, the subtitle's language and target, and what the download does."""

    file_name: str
    language: str
    target_name: str
    target_folder: str
    outcome: PlacementOutcome
    outcome_text: str
    # Why the name differs from the default name (folder names or the media file's extension added), or "".
    note: str


@dataclass(frozen=True)
class FailureRow:
    """A job the last download could not place."""

    target: str
    problem: str


def strategy_text(choice: StrategyChoice) -> str:
    """Return the strategy chooser's text of a choice."""
    texts: dict[StrategyChoice, str] = {
        "next_to_media": tr("Next to the original files"),
        "mirror": tr("Into a folder, keeping the folder structure"),
        "flat": tr("Into one folder"),
        "flat_with_folders": tr("Into one folder, with folder names in the file names"),
        "custom": tr("Custom plan (a JSON file you edit)"),
    }
    return texts[choice]


def outcome_text(outcome: PlacementOutcome) -> str:
    """Return what the download does with a job, as the plan table shows it."""
    texts: dict[PlacementOutcome, str] = {
        "place": tr("Will be placed"),
        "overwrite": tr("Will replace the existing file"),
        "skip_existing": tr("Skipped: the file already exists"),
        "skip_folder_missing": tr("Skipped: the original folder was not found"),
        "skip_empty": tr("Skipped: no speech was found"),
        "skip_failed": tr("Skipped: the transcription failed"),
        "not_finished": tr("Not finished yet"),
    }
    return texts[outcome]


def _split(path: str | None) -> tuple[str, str]:
    """Return a path's folder and name (both "" without a path)."""
    if path is None:
        return "", ""
    pure = PurePath(path)
    return str(pure.parent), pure.name


def _row(entry: PlannedPlacement) -> PlanRow:
    folder, name = _split(entry.target_path)
    reasons = (code_text("rename_reason", reason) for reason in entry.renamed_by)
    return PlanRow(
        file_name=PurePath(entry.source_path).name,
        language=entry.language,
        target_name=name,
        target_folder=folder,
        outcome=entry.outcome,
        outcome_text=outcome_text(entry.outcome),
        note=CELL_SEPARATOR.join(reasons),
    )


def plan_rows(plan: PlacementPlan) -> tuple[PlanRow, ...]:
    """Return the plan's entries as rows, in the plan's order."""
    return tuple(_row(entry) for entry in plan.entries)


def _plan_error_text(error: PlanError) -> str:
    return code_text("plan_error", error.code, error.details, error.message)


def plan_error_texts(plan: PlacementPlan) -> tuple[str, ...]:
    """Return each plan error as the catalog text of its code, formatted with its details."""
    return tuple(_plan_error_text(error) for error in plan.errors)


def _with_count(text: str, count: int) -> str:
    return text.format(count=format_number(count))


def plan_summary(entries: Sequence[PlannedPlacement]) -> str:
    """Return the summary line of a plan: how many subtitles are placed, replaced, skipped, not finished yet."""
    counts = Counter(entry.outcome for entry in entries)
    placed = counts["place"]
    replaced = counts["overwrite"]
    skipped = sum(counts[outcome] for outcome in SKIPPED_OUTCOMES)
    unfinished = counts["not_finished"]
    counted = (
        (ntr("{count} subtitle will be placed", "{count} subtitles will be placed", placed), placed),
        (ntr("{count} file will be replaced", "{count} files will be replaced", replaced), replaced),
        (ntr("{count} skipped", "{count} skipped", skipped), skipped),
        (ntr("{count} not finished yet", "{count} not finished yet", unfinished), unfinished),
    )
    parts = [_with_count(text, count) for text, count in counted]
    return LIST_SEPARATOR.join(parts)


def outcome_summary(outcome: DownloadOutcome) -> str:
    """Return the summary line of a download: files written, skipped, not finished yet, and failures."""
    text = tr(
        "Last download: {written} written, {skipped} skipped, {not_finished} not finished yet, "
        "{failed} could not be placed.",
    )
    counts = {
        "written": outcome.written,
        "skipped": outcome.skipped,
        "not_finished": outcome.not_finished,
        "failed": len(outcome.failures),
    }
    shown = {key: format_number(count) for key, count in counts.items()}
    return text.format_map(shown)


def _failure_row(failure: DownloadFailure) -> FailureRow:
    problem = code_text("download_failure", failure.code, failure.details, failure.message)
    return FailureRow(target=failure.target_path or "", problem=problem)


def failure_rows(outcome: DownloadOutcome) -> tuple[FailureRow, ...]:
    """Return the jobs the download could not place, each with the catalog text of its failure."""
    return tuple(_failure_row(failure) for failure in outcome.failures)


def level_text(level: FolderLevel) -> str:
    """Return a folder level as the level chooser shows it: its number and some of its folder names."""
    names = LIST_SEPARATOR.join(level.names)
    more = level.folders - len(level.names)
    number = format_number(level.level)
    if not more:
        return tr("Level {level}: {names}").format(level=number, names=names)
    text = tr("Level {level}: {names} and {more} more")
    return text.format(level=number, names=names, more=format_number(more))
