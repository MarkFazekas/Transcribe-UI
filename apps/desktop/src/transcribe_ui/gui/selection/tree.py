"""The scan as a tree: an index over the flat `scan_tree` response (architecture §3.2).

Qt-free. The response lists folders depth first (each with its parent's index) and files
grouped by folder in that order, so one pass over each list gives every folder's child folders
and the range of its files. A node is a number: folders keep their index, files follow them
(`folder count + file index`), so the item model needs no object per row and a 20 000-file scan
costs two short loops. Under a folder its child folders come first, then its files, both in
the scan's order.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from itertools import repeat
from types import MappingProxyType

from transcribe_ui.discovery.classes import (
    PreviewSelectionRequest,
    ScanFile,
    ScanSpec,
    ScanTreeResponse,
    SelectionOperation,
)


def _folder_of(found: ScanFile) -> int:
    return found.folder


# Numbers of nodes, one per folder (each folder's first file, row, ...).
type PerFolder = tuple[int, ...]


def _folder_children(tree: ScanTreeResponse) -> tuple[tuple[PerFolder, ...], PerFolder]:
    """Return each folder's child folders and each folder's row under its parent."""
    count = len(tree.folders)
    children: list[list[int]] = [[] for _folder in range(count)]
    rows = list(repeat(0, count))
    for number, folder in enumerate(tree.folders):
        if folder.parent is not None:
            siblings = children[folder.parent]
            rows[number] = len(siblings)
            siblings.append(number)
    return tuple(tuple(folders) for folders in children), tuple(rows)


def _folder_files(tree: ScanTreeResponse) -> tuple[PerFolder, PerFolder]:
    """Return each folder's first file and its number of files (the files of a folder follow one another)."""
    first_files = list(repeat(0, len(tree.folders)))
    file_counts = list(repeat(0, len(tree.folders)))
    for number, scanned in enumerate(tree.files):
        folder = scanned.folder
        if not file_counts[folder]:
            first_files[folder] = number
        file_counts[folder] += 1
    return tuple(first_files), tuple(file_counts)


@dataclass(frozen=True)
class ScanIndex:
    """Parents, rows and children of every folder and file of one scan."""

    tree: ScanTreeResponse
    # Number of folders: the first file's node.
    folder_count: int
    # Child folders of each folder, in the scan's order.
    child_folders: tuple[tuple[int, ...], ...]
    # Row of each folder under its parent (0 for the scan root).
    folder_rows: tuple[int, ...]
    # Index of each folder's first file.
    first_files: tuple[int, ...]
    # Number of files directly in each folder.
    file_counts: tuple[int, ...]

    @classmethod
    def of(cls, tree: ScanTreeResponse) -> "ScanIndex":
        """Index a scan response."""
        children, rows = _folder_children(tree)
        first_files, file_counts = _folder_files(tree)
        return cls(tree, len(tree.folders), children, rows, first_files, file_counts)

    def child_count(self, node: int | None) -> int:
        """Return the rows under a folder (None: the top level, which holds the scan root); a file has none."""
        if node is None:
            return min(self.folder_count, 1)
        if not self.is_folder(node):
            return 0
        return len(self.child_folders[node]) + self.file_counts[node]

    def child(self, node: int | None, row: int) -> int:
        """Return the node at `row` under a folder (None: the scan root at the top level)."""
        if node is None:
            return 0
        folders = self.child_folders[node]
        if row < len(folders):
            return folders[row]
        position = self.first_files[node] + row - len(folders)
        return self.folder_count + position

    def parent(self, node: int) -> int | None:
        """Return the folder holding a node (None for the scan root)."""
        found = self.media_file(node)
        if found is None:
            return self.tree.folders[node].parent
        return found.folder

    def row(self, node: int) -> int:
        """Return the row of a node under its parent."""
        found = self.media_file(node)
        if found is None:
            return self.folder_rows[node]
        holder = found.folder
        position = node - self.folder_count - self.first_files[holder]
        return len(self.child_folders[holder]) + position

    @cached_property
    def nodes(self) -> Mapping[str, int]:
        """Return the node of every path (built once, when first needed)."""
        nodes: dict[str, int] = {}
        for number, folder in enumerate(self.tree.folders):
            nodes[folder.path] = number
        for number, found in enumerate(self.tree.files, start=self.folder_count):
            nodes[found.path] = number
        return MappingProxyType(nodes)

    def files_below(self, node: int) -> tuple[ScanFile, ...]:
        """Return the files at or below a folder (folders are depth first, so their files follow one another)."""
        last = node
        while self.child_folders[last]:
            last = self.child_folders[last][-1]
        files = self.tree.files
        first = bisect_left(files, node, key=_folder_of)
        end = bisect_right(files, last, key=_folder_of)
        return files[first:end]

    def is_folder(self, node: int) -> bool:
        """Return whether a node is a folder."""
        return node < self.folder_count

    def media_file(self, node: int) -> ScanFile | None:
        """Return a file node's file, or None for a folder."""
        if self.is_folder(node):
            return None
        return self.tree.files[node - self.folder_count]

    def path(self, node: int) -> str:
        """Return a node's path as the scan reported it."""
        found = self.media_file(node)
        return self.tree.folders[node].path if found is None else found.path


@dataclass(frozen=True)
class Scanned:
    """A scan on screen: the options it was made with and its index."""

    spec: ScanSpec
    index: ScanIndex

    def preview_request(self, operations: tuple[SelectionOperation, ...]) -> PreviewSelectionRequest:
        """Return the preview of `operations` on this scan (the backend reuses the scan it keeps)."""
        return PreviewSelectionRequest(
            action="preview_selection",
            scan=self.spec,
            scan_id=self.index.tree.scan_id,
            operations=operations,
        )
