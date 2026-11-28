"""Which rows of the tree a change of the page shows differently (architecture §3.1 "GUI" "New job").

Qt-free. The item model keeps a snapshot of what its cells show (`Shown`: the preview's marks,
the clicks waiting for an answer, the filters' matches, the target languages) and, after a
change, compares it with the new one, so it tells the view only about the rows that changed:
a click costs as much as what it changed, not as much as the tree. Turning the filters on or
off can change the dimming of any row; only then is everything repainted.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from transcribe_ui.gui.selection.constants import CheckMark, Languages
from transcribe_ui.gui.selection.marks import Marks

if TYPE_CHECKING:
    from transcribe_ui.gui.selection.tree import ScanIndex
    from transcribe_ui.gui.selection.viewmodel import NewJobModel


@dataclass(frozen=True)
class Shown:
    """What the tree's cells show, apart from the scan itself."""

    marks: Marks
    pending: Mapping[str, CheckMark]
    # What the active filters match (None: no filter, nothing dimmed).
    matches: Marks | None
    languages: Languages
    own: Mapping[str, Languages]


@dataclass(frozen=True)
class Changed:
    """The paths whose rows show something else now."""

    # Rows that changed in any column.
    rows: frozenset[str] = frozenset()
    # Files whose target languages changed (only that column).
    targets: frozenset[str] = frozenset()
    # Every row may have changed (the filters were turned on or off).
    everything: bool = False

    def __bool__(self) -> bool:
        """Return whether anything changed."""
        return self.everything or bool(self.rows or self.targets)


def shown_of(model: "NewJobModel") -> Shown:
    """Return what the page's tree shows now."""
    ticks = model.ticks
    targets = model.targets
    scanned = model.scan.scanned
    matches = None
    if scanned is not None:
        matches = model.filters.matches_on(scanned.index.tree.root)
    return Shown(ticks.marks, ticks.pending, matches, targets.languages, targets.own_map())


# Values per path, as a snapshot keeps them.
type ByPath = Mapping[str, object]


def _differing(old: ByPath, new: ByPath) -> set[str]:
    """Return the keys of either mapping whose values differ (a missing key differs)."""
    differing = set()
    for key in old.keys() | new.keys():
        if old.get(key) != new.get(key):
            differing.add(key)
    return differing


def _marks_changed(old: Marks, new: Marks) -> set[str]:
    if old is new:
        return set()
    files = old.files ^ new.files
    return _differing(old.folders, new.folders) | files


def _matches_changed(old: Marks | None, new: Marks | None) -> set[str] | None:
    """Return the rows whose dimming changed, or None when any row's may have."""
    if old is None or new is None:
        return set() if old is new else None
    return _marks_changed(old, new)


def changed_cells(old: Shown, new: Shown) -> Changed:
    """Return the rows that show something else in `new` than in `old`."""
    dimmed = _matches_changed(old.matches, new.matches)
    if dimmed is None:
        return Changed(everything=True)
    clicked = _differing(old.pending, new.pending)
    rows = _marks_changed(old.marks, new.marks) | clicked | dimmed
    targets = _differing(old.own, new.own)
    if old.languages != new.languages:
        targets |= new.marks.files
    return Changed(frozenset(rows), frozenset(targets - rows))


@dataclass(frozen=True)
class RowRange:
    """Rows next to one another under one folder (None: the top level)."""

    holder: int | None
    first: int
    last: int


def row_ranges(index: "ScanIndex", paths: frozenset[str]) -> tuple[RowRange, ...]:
    """Return, per folder holding one of `paths`, the range of its rows they span (paths not in the scan skipped)."""
    spans: dict[int | None, tuple[int, int]] = {}
    for path in paths:
        node = index.nodes.get(path)
        if node is None:
            continue
        holder = index.parent(node)
        row = index.row(node)
        first, last = spans.get(holder, (row, row))
        spans[holder] = (min(first, row), max(last, row))
    ranges = []
    for holder, (first, last) in spans.items():
        ranges.append(RowRange(holder, first, last))
    return tuple(ranges)
