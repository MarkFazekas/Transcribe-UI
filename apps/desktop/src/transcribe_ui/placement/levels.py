"""The folder levels a batch's files have below its scan root, for choosing `folder_levels` (architecture §3.5).

A frontend offers these levels when the user picks the folders `flat_with_folders` joins into
the names, so it never splits paths itself: the levels come from the same split the planner
uses (the batch's path flavour, its scan root, the target's rule for equal names).
"""

from collections.abc import Sequence

from transcribe_ui.discovery.rules import PathRules
from transcribe_ui.placement.classes import FolderLevel, PlacementJob
from transcribe_ui.placement.constants import FOLDER_LEVEL_NAMES
from transcribe_ui.placement.drafts import source_of
from transcribe_ui.placement.naming import TargetNames

# The distinct folder names of one level by their rule key (the first spelling seen is shown).
type LevelNames = dict[str, str]


def _sort_key(name: str) -> tuple[str, str]:
    return name.casefold(), name


def _level(level: int, names: LevelNames) -> FolderLevel:
    shown = sorted(names.values(), key=_sort_key)
    first = tuple(shown[:FOLDER_LEVEL_NAMES])
    return FolderLevel(level=level, names=first, folders=len(shown))


def _add_folders(levels: list[LevelNames], folders: Sequence[str], rules: PathRules) -> None:
    """Add a file's folders (from the scan root down) to the names of their levels."""
    for depth, folder in enumerate(folders):
        if depth == len(levels):
            levels.append({})
        levels[depth].setdefault(rules.key(folder), folder)


def folder_levels(jobs: Sequence[PlacementJob], scan_root: str, rules: PathRules) -> tuple[FolderLevel, ...]:
    """Return every folder level below the scan root that a job's file has, with some of its folder names.

    Level 1 is the first folder below the scan root. A file outside the scan root, or whose
    path is not absolute without `..`, adds no level (the plan reports it).
    """
    names = TargetNames(rules)
    root = names.pure(scan_root)
    levels: list[LevelNames] = []
    for job in jobs:
        _add_folders(levels, source_of(job, root, names).relative or (), rules)
    numbered = enumerate(levels, start=1)
    return tuple(_level(number, found) for number, found in numbered)
