"""The placement planner: strategies 1-4 of architecture §3.5 as a validated plan.

Pure apart from the injected probe (existing targets, the original folder, link resolution):
the same jobs, options, rules and probe answers give the same plan. Nothing is written; a plan
with errors must not be carried out.
"""

from collections import Counter
from collections.abc import Sequence
from pathlib import PurePath

from transcribe_ui.discovery.rules import PathRules
from transcribe_ui.placement.classes import (
    OutcomeOptions,
    PlacementJob,
    PlacementOptions,
    PlacementOutcome,
    PlacementPlan,
    PlanError,
    PlannedPlacement,
)
from transcribe_ui.placement.constants import (
    ERROR_DUPLICATE_TARGET,
    ERROR_NAME_TOO_LONG,
    ERROR_OUTPUT_NOT_ABSOLUTE,
    ERROR_OUTSIDE_SCAN_ROOT,
    ERROR_PATH_TOO_LONG,
    ERROR_TARGET_NOT_A_FILE,
    FLAT_STRATEGIES,
    MAX_NAME_LENGTH,
    WRITING_OUTCOMES,
)
from transcribe_ui.placement.drafts import Draft, Source, closest_levels, resolve_collisions, source_of
from transcribe_ui.placement.naming import TargetNames
from transcribe_ui.placement.probe import PlacementProbe


def job_label(job: PlacementJob) -> str:
    """Return how messages name a job: its file name and language (no folders: see `PlanError`)."""
    name = PurePath(job.source_path.replace("\\", "/")).name
    return f"{name} ({job.language})"


def job_error(code: str, message: str, job: PlacementJob) -> PlanError:
    """Return a plan error about one job."""
    text = f"{job_label(job)}: {message}"
    return PlanError(code=code, message=text, item_id=job.item_id, language=job.language)


def output_error() -> PlanError:
    """Return the error of an output folder that is not an absolute path."""
    return PlanError(code=ERROR_OUTPUT_NOT_ABSOLUTE, message="The output folder is not an absolute path.")


def result_outcome(job: PlacementJob, options: OutcomeOptions) -> PlacementOutcome | None:
    """Return the outcome of a job whose subtitle is not placed (no result, failed, empty), or None."""
    if job.subtitle == "pending":
        return "not_finished"
    if job.subtitle == "failed":
        return "skip_failed"
    if job.subtitle == "empty" and not options.include_empty:
        return "skip_empty"
    return None


def target_outcome(
    target: PurePath,
    options: OutcomeOptions,
    probe: PlacementProbe,
    *,
    needs_folder: bool,
) -> PlacementOutcome:
    """Return what a download does with a placeable subtitle's target."""
    if needs_folder and not probe.folder_exists(str(target.parent)):
        return "skip_folder_missing"
    if probe.target_kind(str(target)) == "missing":
        return "place"
    return "overwrite" if options.overwrite else "skip_existing"


def target_errors(
    entry: PlannedPlacement, job: PlacementJob, names: TargetNames, probe: PlacementProbe
) -> list[PlanError]:
    """Return the problems of a writing entry's target: name and path length, a folder in the way."""
    target = entry.target_path
    if entry.outcome not in WRITING_OUTCOMES or target is None:
        return []
    errors: list[PlanError] = []
    name = names.pure(target).name
    if names.length(name) > MAX_NAME_LENGTH:
        message = f"the name {name!r} is longer than {MAX_NAME_LENGTH}"
        errors.append(job_error(ERROR_NAME_TOO_LONG, message, job))
    if names.path_too_long(target):
        errors.append(job_error(ERROR_PATH_TOO_LONG, "the target path is longer than Windows allows", job))
    in_the_way = entry.outcome == "overwrite" and probe.target_kind(target) == "other"
    if in_the_way:
        errors.append(job_error(ERROR_TARGET_NOT_A_FILE, f"{name!r} exists and is not a file", job))
    return errors


def duplicate_errors(
    entries: Sequence[PlannedPlacement],
    jobs: Sequence[PlacementJob],
    keys: Sequence[object | None],
) -> list[PlanError]:
    """Return an error for each writing entry whose target key another entry shares."""
    counts = Counter(key for key in keys if key is not None)
    errors: list[PlanError] = []
    for entry, job, key in zip(entries, jobs, keys, strict=True):
        shared = key is not None and counts[key] > 1
        if shared and entry.outcome in WRITING_OUTCOMES:
            errors.append(job_error(ERROR_DUPLICATE_TARGET, "another job has the same target", job))
    return errors


def _flat_levels(source: Source, options: PlacementOptions) -> frozenset[int]:
    if options.strategy == "flat":
        return frozenset()
    if options.folder_levels:
        return frozenset(level for level in options.folder_levels if level <= source.depth)
    return closest_levels(source.depth, options.folder_count)


def _draft(source: Source, options: PlacementOptions, output: PurePath | None, names: TargetNames) -> Draft:
    if options.strategy == "next_to_media":
        return Draft(source=source, folder=source.folder)
    outside_root = source.relative is None and options.strategy != "flat"
    if output is None or outside_root:
        return Draft(source=source, folder=None)
    if options.strategy == "mirror":
        folders = map(names.sanitised, source.relative or ())
        return Draft(source=source, folder=output.joinpath(*folders))
    return Draft(source=source, folder=output, levels=_flat_levels(source, options))


def _entry(draft: Draft, options: PlacementOptions, names: TargetNames, probe: PlacementProbe) -> PlannedPlacement:
    job = draft.source.job
    target = draft.target(names)
    outcome = result_outcome(job, options)
    if outcome is None and target is not None:
        next_to_media = options.strategy == "next_to_media"
        outcome = target_outcome(target, options, probe, needs_folder=next_to_media)
    return PlannedPlacement(
        item_id=job.item_id,
        language=job.language,
        source_path=job.source_path,
        target_path=None if target is None else str(target),
        # Without a target the plan has an error, so "place" is never carried out.
        outcome=outcome or "place",
        renamed_by=tuple(draft.renamed_by),
    )


def _entry_errors(draft: Draft, entry: PlannedPlacement, names: TargetNames, probe: PlacementProbe) -> list[PlanError]:
    job = draft.source.job
    errors = target_errors(entry, job, names, probe)
    if draft.folder is None and entry.outcome in WRITING_OUTCOMES:
        errors.append(job_error(ERROR_OUTSIDE_SCAN_ROOT, "the file is not below the scan root", job))
    return errors


def plan_placement(
    jobs: Sequence[PlacementJob],
    scan_root: str,
    options: PlacementOptions,
    rules: PathRules,
    probe: PlacementProbe,
) -> PlacementPlan:
    """Return the plan of placing the jobs' subtitles with a strategy of §3.5 (one entry per job, in order)."""
    names = TargetNames(rules)
    output = None
    if options.output_folder is not None:
        output = names.pure(options.output_folder)
        if not output.is_absolute():
            return PlacementPlan(entries=(), errors=(output_error(),))
    root = names.pure(scan_root)
    sources = [source_of(job, root, names) for job in jobs]
    drafts = [_draft(source, options, output, names) for source in sources]
    resolve_collisions(drafts, names, by_folders=options.strategy in FLAT_STRATEGIES)
    entries = [_entry(draft, options, names, probe) for draft in drafts]
    errors: list[PlanError] = []
    for planned, entry in zip(drafts, entries, strict=True):
        errors.extend(_entry_errors(planned, entry, names, probe))
    keys = [draft.key(names) for draft in drafts]
    errors.extend(duplicate_errors(entries, jobs, keys))
    return PlacementPlan(entries=tuple(entries), errors=tuple(errors))
