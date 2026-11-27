"""Custom placement plans: export for the user (or an LLM) to edit, validated import (architecture §3.5).

The export lists every job once, pre-filled with the mirror strategy's folders (relative to the
output folder chosen at import) and the default names. A source the mirror strategy cannot
place (outside the scan root, or not an absolute path without `..`) is pre-filled with an empty
folder (the output folder itself), for the user to fill in. The import checks the schema, every job
exactly once with its source unchanged, the names against the target OS's rules, the `.srt`
extension, and that every target, after resolving `..` and symbolic links, lies inside the
chosen output folder; then it gives the same plan as the other strategies.
"""

from collections.abc import Iterator, Mapping, Sequence
from pathlib import PurePath
from typing import TYPE_CHECKING

from pydantic import JsonValue, ValidationError

from transcribe_ui.discovery.rules import PathRules
from transcribe_ui.placement.classes import (
    CustomPlacementOptions,
    CustomPlan,
    CustomPlanEntry,
    PlacementJob,
    PlacementPlan,
    PlanError,
    PlannedPlacement,
)
from transcribe_ui.placement.constants import (
    BOM_UTF8,
    BYTE_ORDER_MARK,
    ERROR_CUSTOM_DUPLICATE_JOB,
    ERROR_CUSTOM_INVALID_NAME,
    ERROR_CUSTOM_MISSING_JOB,
    ERROR_CUSTOM_NOT_SRT,
    ERROR_CUSTOM_OUTSIDE_ROOT,
    ERROR_CUSTOM_SCHEMA,
    ERROR_CUSTOM_SOURCE_CHANGED,
    ERROR_CUSTOM_UNKNOWN_JOB,
)
from transcribe_ui.placement.drafts import Draft, resolve_collisions, source_of
from transcribe_ui.placement.naming import PathKey, TargetNames, has_subtitle_extension
from transcribe_ui.placement.planner import (
    duplicate_errors,
    job_error,
    name_errors,
    output_error,
    result_outcome,
    target_errors,
    target_outcome,
)
from transcribe_ui.placement.probe import PlacementProbe

if TYPE_CHECKING:
    from transcribe_ui.api.errors import Details

# A job's identity in a custom plan: its item and language.
type JobKey = tuple[str, str]


def export_custom_plan(jobs: Sequence[PlacementJob], scan_root: str, rules: PathRules) -> CustomPlan:
    """Return the custom plan of the jobs, pre-filled with the mirror strategy's folders and names."""
    names = TargetNames(rules)
    root = names.pure(scan_root)
    drafts = [_export_draft(job, root, names) for job in jobs]
    resolve_collisions(drafts, names, by_folders=False)
    return CustomPlan(entries=tuple(_export_entry(draft, names) for draft in drafts))


def _export_draft(job: PlacementJob, root: PurePath, names: TargetNames) -> Draft:
    source = source_of(job, root, names)
    folders = map(names.sanitised, source.relative or ())
    here = names.pure("")
    return Draft(source=source, folder=here.joinpath(*folders))


def _export_entry(draft: Draft, names: TargetNames) -> CustomPlanEntry:
    job = draft.source.job
    folder = draft.folder or names.pure("")
    return CustomPlanEntry(
        item_id=job.item_id,
        language=job.language,
        source_path=job.source_path,
        original_folder=str(draft.source.folder),
        output_folder=str(folder) if folder.parts else "",
        output_file_name=draft.name(names),
    )


def _schema_problems(error: ValidationError) -> Iterator[str]:
    # Locations and messages only: the input values may hold paths.
    for problem in error.errors(include_url=False, include_input=False, include_context=False):
        location = ".".join(map(str, problem["loc"])) or "plan"
        yield f"{location}: {problem['msg']}"


def _schema_error(error: ValidationError) -> PlanError:
    listed = list(_schema_problems(error))
    message = f"The custom plan does not match its format: {'; '.join(listed)}"
    shown: list[JsonValue] = []
    shown.extend(listed)
    return PlanError(code=ERROR_CUSTOM_SCHEMA, message=message, details={"problems": shown})


def _unknown_error(entry: CustomPlanEntry) -> PlanError:
    message = f"plan entry {entry.item_id} ({entry.language}) is not a job of this download"
    return PlanError(
        code=ERROR_CUSTOM_UNKNOWN_JOB,
        message=message,
        item_id=entry.item_id,
        language=entry.language,
        details={"item_id": entry.item_id, "language": entry.language},
    )


def _source_unchanged(entry: CustomPlanEntry, job: PlacementJob, names: TargetNames) -> bool:
    folder = str(names.pure(job.source_path).parent)
    return entry.source_path == job.source_path and entry.original_folder == folder


def _listing_errors(plan: CustomPlan, jobs: Mapping[JobKey, PlacementJob], names: TargetNames) -> list[PlanError]:
    # Every job exactly once, nothing unknown, the source unchanged.
    errors: list[PlanError] = []
    seen: set[JobKey] = set()
    for entry in plan.entries:
        key = (entry.item_id, entry.language)
        job = jobs.get(key)
        if job is None:
            errors.append(_unknown_error(entry))
        elif key in seen:
            errors.append(job_error(ERROR_CUSTOM_DUPLICATE_JOB, "listed more than once", job))
        elif not _source_unchanged(entry, job, names):
            errors.append(job_error(ERROR_CUSTOM_SOURCE_CHANGED, "source_path or original_folder was changed", job))
        seen.add(key)
    missing = (job for key, job in jobs.items() if key not in seen)
    errors.extend(job_error(ERROR_CUSTOM_MISSING_JOB, "missing from the plan", job) for job in missing)
    return errors


def _entry_name_errors(entry: CustomPlanEntry, job: PlacementJob, names: TargetNames) -> list[PlanError]:
    folder = names.pure(entry.output_folder)
    skipped = frozenset((".", "..", folder.anchor))
    checked = [part for part in folder.parts if part not in skipped]
    checked.append(entry.output_file_name)
    errors: list[PlanError] = []
    for name in checked:
        problem = names.problem(name)
        if problem is not None:
            shown: Details = {"name": name, "problem": problem}
            message = f"the name {name!r} {problem}"
            errors.append(job_error(ERROR_CUSTOM_INVALID_NAME, message, job, shown))
    if not has_subtitle_extension(entry.output_file_name):
        errors.append(job_error(ERROR_CUSTOM_NOT_SRT, "output_file_name does not end with .srt", job))
    return errors


def _inside(target: PathKey, root: PathKey) -> bool:
    depth = len(root)
    return len(target) > depth and target[:depth] == root


class _Importer:
    """Turns a listed custom plan into placement entries and errors."""

    def __init__(self, options: CustomPlacementOptions, names: TargetNames, probe: PlacementProbe) -> None:
        self._options = options
        self._names = names
        self._probe = probe
        self._root = names.path_key(probe.resolve(options.output_folder))
        self.entries: list[PlannedPlacement] = []
        self._keys: list[PathKey | None] = []
        self.errors: list[PlanError] = []

    def add(self, entry: CustomPlanEntry, job: PlacementJob) -> None:
        """Validate one entry and add its placement."""
        invalid = _entry_name_errors(entry, job, self._names)
        self.errors.extend(invalid)
        target = None
        if invalid:
            self._keys.append(None)
        else:
            target = self._target(entry, job)
        outcome = result_outcome(job, self._options)
        if outcome is None and target is not None:
            target_path = self._names.pure(target)
            outcome = target_outcome(target_path, self._options, self._probe, needs_folder=False)
        placed = PlannedPlacement(
            item_id=job.item_id,
            language=job.language,
            source_path=job.source_path,
            target_path=target,
            # Without a target the plan has an error, so "place" is never carried out.
            outcome=outcome or "place",
        )
        self.errors.extend(name_errors(target, job, self._names))
        self.errors.extend(target_errors(placed, job, self._probe))
        self.entries.append(placed)

    def plan(self, jobs: Sequence[PlacementJob]) -> PlacementPlan:
        """Return the plan with duplicate targets reported."""
        duplicates = duplicate_errors(jobs, self._keys)
        errors = (*self.errors, *duplicates)
        return PlacementPlan(entries=tuple(self.entries), errors=errors)

    def _target(self, entry: CustomPlanEntry, job: PlacementJob) -> str:
        folder = self._names.pure(self._options.output_folder) / entry.output_folder
        joined = folder / entry.output_file_name
        target = self._names.normalised(str(joined))
        resolved = self._names.path_key(self._probe.resolve(target))
        self._keys.append(resolved)
        if not _inside(resolved, self._root):
            self.errors.append(job_error(ERROR_CUSTOM_OUTSIDE_ROOT, "the target is outside the output folder", job))
        return target


def _without_bom(document: str | bytes) -> str | bytes:
    if isinstance(document, str):
        return document.removeprefix(BYTE_ORDER_MARK)
    return document.removeprefix(BOM_UTF8)


def import_custom_plan(
    document: str | bytes,
    jobs: Sequence[PlacementJob],
    options: CustomPlacementOptions,
    rules: PathRules,
    probe: PlacementProbe,
) -> PlacementPlan:
    """Return the plan of an edited custom plan, validated against the jobs and the output folder.

    Entries follow the plan's order; a plan with errors must not be carried out.
    """
    # Editors on Windows often save UTF-8 with a byte order mark.
    unmarked = _without_bom(document)
    try:
        plan = CustomPlan.model_validate_json(unmarked)
    except ValidationError as error:
        return PlacementPlan(entries=(), errors=(_schema_error(error),))
    names = TargetNames(rules)
    if not names.pure(options.output_folder).is_absolute():
        return PlacementPlan(entries=(), errors=(output_error(),))
    by_key = {(job.item_id, job.language): job for job in jobs}
    listing = _listing_errors(plan, by_key, names)
    if listing:
        return PlacementPlan(entries=(), errors=tuple(listing))
    importer = _Importer(options, names, probe)
    listed = [by_key[entry.item_id, entry.language] for entry in plan.entries]
    for entry, job in zip(plan.entries, listed, strict=True):
        importer.add(entry, job)
    return importer.plan(listed)
