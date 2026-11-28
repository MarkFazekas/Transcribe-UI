"""What one row of the tree shows, for the item model (architecture §3.1 "GUI").

Qt-free and lazy: the item model asks only for the rows on screen, so a large scan costs
nothing until it is scrolled to. A file row shows its name, the subtitles it already has, its
kind, its size and, while selected, the languages it is transcribed into; a folder row its
name (the scan root its whole path). The tick box comes from the selection, the dimming from
what the active filters match.
"""

from transcribe_ui.discovery.classes import ScanFile
from transcribe_ui.gui.selection.constants import KIND_COLUMN, NAME_COLUMN, SIZE_COLUMN, SUBTITLES_COLUMN, CheckMark
from transcribe_ui.gui.selection.display import kind_text, size_text, subtitles_text, subtitles_tooltip, targets_text
from transcribe_ui.gui.selection.tree import ScanIndex
from transcribe_ui.gui.selection.viewmodel import NewJobModel


def _file_text(model: NewJobModel, scanned: ScanFile, column: int) -> str:
    targets = model.targets
    cells = {
        NAME_COLUMN: lambda: scanned.name,
        SUBTITLES_COLUMN: lambda: subtitles_text(scanned),
        KIND_COLUMN: lambda: kind_text(scanned.kind),
        SIZE_COLUMN: lambda: size_text(scanned.size_bytes),
    }
    shown = cells.get(column)
    if shown is not None:
        return shown()
    if scanned.path not in model.ticks.marks.files:
        return ""
    path = scanned.path
    own = targets.own(path) is not None
    return targets_text(targets.of(path), own=own)


def cell_text(model: NewJobModel, index: ScanIndex, node: int, column: int) -> str:
    """Return the text of a row's cell."""
    scanned = index.media_file(node)
    if scanned is not None:
        return _file_text(model, scanned, column)
    if column != NAME_COLUMN:
        return ""
    if not node:
        return index.tree.root
    return index.tree.folders[node].name


def row_mark(model: NewJobModel, index: ScanIndex, node: int) -> CheckMark:
    """Return a row's tick box."""
    folder = index.is_folder(node)
    return model.ticks.mark(index.path(node), folder=folder)


def row_dimmed(model: NewJobModel, index: ScanIndex, node: int) -> bool:
    """Return whether a row is dimmed: the active filters match neither the file nor anything below the folder."""
    folder = index.is_folder(node)
    return not model.filters.matching(index.path(node), folder=folder)


def cell_tooltip(index: ScanIndex, node: int, column: int) -> str:
    """Return a cell's tooltip: a file's subtitle files on the subtitles cell, else the row's path."""
    scanned = index.media_file(node)
    if scanned is not None and column == SUBTITLES_COLUMN:
        return subtitles_tooltip(scanned)
    return index.path(node)


def tick_row(model: NewJobModel, index: ScanIndex, node: int) -> None:
    """Tick or untick a row (the view-model appends the operation)."""
    folder = index.is_folder(node)
    model.tick(index.path(node), folder=folder)
