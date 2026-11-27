"""The folder walk behind `scan_tree` and `preview_selection` (architecture §3.2).

`os.scandir` without following links (symlinks and Windows junctions are reported and skipped),
hidden entries left out unless asked, entries classified by extension; no ffprobe. The walk is
iterative (an explicit stack), so deep trees never reach the interpreter's recursion limit.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path, PurePath

from transcribe_contracts.classes import MediaKind
from transcribe_ui.api.classes import ProgressEvent
from transcribe_ui.api.errors import DomainError
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.discovery.classes import (
    ScanCounts,
    ScanFile,
    ScanFolder,
    ScanSpec,
    ScanTreeResponse,
    Sidecar,
)
from transcribe_ui.discovery.constants import (
    ERROR_ROOT_NOT_A_FOLDER,
    ERROR_ROOT_NOT_ABSOLUTE,
    ERROR_ROOT_NOT_FOUND,
    MAX_SCAN_DEPTH,
    SCAN_PROGRESS_INTERVAL,
    SCAN_STAGE,
    SIDECAR_EXTENSIONS,
)
from transcribe_ui.discovery.entries import EntryReader
from transcribe_ui.discovery.rules import PathRules
from transcribe_ui.discovery.sidecars import NamedPath, match_sidecars, stem_index


@dataclass
class _Media:
    name: str
    path: str
    kind: MediaKind
    size_bytes: int
    mtime_ns: int
    subtitles: tuple[Sidecar, ...] = ()


@dataclass
class _Folder:
    name: str
    path: str
    depth: int
    media: list[_Media] = field(default_factory=list)
    children: list["_Folder"] = field(default_factory=list)
    subtitle_files: int = 0

    def has_media(self) -> bool:
        # Called bottom-up after the walk, when children without media are already pruned.
        return bool(self.media or self.children)


def checked_root(root: str) -> str:
    """Return the scan root normalised; refuse a relative, missing or non-folder root.

    Raises:
        DomainError: If the root is not an absolute path of an existing folder.
    """
    if not Path(root).is_absolute():
        raise DomainError(ERROR_ROOT_NOT_ABSOLUTE, "The folder to scan must be given as an absolute path.")
    normalised = os.path.normpath(root)
    if Path(normalised).is_dir():
        return normalised
    if Path(normalised).exists():
        raise DomainError(ERROR_ROOT_NOT_A_FOLDER, "The path to scan is not a folder.")
    raise DomainError(ERROR_ROOT_NOT_FOUND, "The folder to scan does not exist.")


class _Walker:
    """Walks one tree; reports progress, the reader collects the warnings."""

    def __init__(self, spec: ScanSpec, rules: PathRules, context: RequestContext, action: str) -> None:
        video: dict[str, MediaKind] = dict.fromkeys(spec.video_extensions, "video")
        audio: dict[str, MediaKind] = dict.fromkeys(spec.audio_extensions, "audio")
        self._kinds = video | audio
        self._rules = rules
        self._context = context
        self._action = action
        self._found = 0
        self.reader = EntryReader(include_hidden=spec.include_hidden)

    def walk(self, root: str) -> _Folder:
        """Return the root folder with its media files and the subfolders that hold media."""
        tree = _Folder(Path(root).name or root, root, depth=0)
        visited: list[_Folder] = []
        pending = [tree]
        while pending:
            node = pending.pop()
            self._context.check_cancelled()
            visited.append(node)
            self._fill(node, is_root=node is tree)
            pending.extend(reversed(node.children))
        # Children come after their parent in `visited`, so this prunes bottom-up.
        for node in reversed(visited):
            node.children = [child for child in node.children if child.has_media()]
        return tree

    def _fill(self, node: _Folder, *, is_root: bool) -> None:
        subtitles: list[NamedPath] = []
        for entry in self.reader.entries(node.path, is_root=is_root):
            kind = self.reader.kind_of(entry)
            if kind == "folder":
                self._add_folder(node, entry)
            elif kind == "file":
                self._classify(node, entry, subtitles)
        self._attach(node, subtitles)

    def _add_folder(self, node: _Folder, entry: os.DirEntry[str]) -> None:
        if node.depth >= MAX_SCAN_DEPTH:
            self.reader.warn("too_deep", entry.path, f"Folders deeper than {MAX_SCAN_DEPTH} levels are not scanned.")
            return
        child = _Folder(entry.name, entry.path, node.depth + 1)
        node.children.append(child)

    def _classify(self, node: _Folder, entry: os.DirEntry[str], subtitles: list[NamedPath]) -> None:
        extension = PurePath(entry.name).suffix[1:].lower()
        kind = self._kinds.get(extension)
        if kind is not None:
            self._add_media(node, entry, kind)
        elif extension in SIDECAR_EXTENSIONS:
            subtitles.append((entry.name, entry.path))

    def _add_media(self, node: _Folder, entry: os.DirEntry[str], kind: MediaKind) -> None:
        try:
            entry_stat = entry.stat(follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError:
            self.reader.warn("unreadable", entry.path, "The entry could not be read.")
            return
        media = _Media(entry.name, entry.path, kind, entry_stat.st_size, entry_stat.st_mtime_ns)
        node.media.append(media)
        self._found += 1
        if self._found % SCAN_PROGRESS_INTERVAL == 0:
            found, folder = self._found, node.path
            event = ProgressEvent(action=self._action, stage=SCAN_STAGE, completed=found, path=folder)
            self._context.progress.emit(event)

    def _attach(self, node: _Folder, subtitles: list[NamedPath]) -> None:
        if not node.media or not subtitles:
            return
        stems = stem_index((media.name for media in node.media), self._rules)
        found = match_sidecars(subtitles, stems, self._rules)
        for media in node.media:
            media.subtitles = tuple(found.get(media.name, ()))
        attached = {sidecar.name for sidecars in found.values() for sidecar in sidecars}
        node.subtitle_files = len(attached)


def _flatten(root: _Folder) -> tuple[list[ScanFolder], list[ScanFile]]:
    """Return the folders in depth-first order and the files grouped by folder in that order."""
    folders: list[ScanFolder] = []
    files: list[ScanFile] = []
    pending: list[tuple[_Folder, int | None]] = [(root, None)]
    while pending:
        node, parent = pending.pop()
        index = len(folders)
        folder = ScanFolder(name=node.name, path=node.path, parent=parent)
        folders.append(folder)
        files.extend(
            ScanFile(
                name=media.name,
                path=media.path,
                folder=index,
                kind=media.kind,
                size_bytes=media.size_bytes,
                mtime_ns=media.mtime_ns,
                subtitles=media.subtitles,
            )
            for media in node.media
        )
        pending.extend((child, index) for child in reversed(node.children))
    return folders, files


def _subtitle_files(root: _Folder) -> int:
    total = 0
    pending = [root]
    while pending:
        node = pending.pop()
        total += node.subtitle_files
        pending.extend(node.children)
    return total


def scan(spec: ScanSpec, context: RequestContext, action: str) -> ScanTreeResponse:
    """Walk the tree below `spec.root`; progress events carry `action`; the scan gets a new id.

    Raises:
        DomainError: If the root is not an absolute path of a readable folder.
    """
    root = checked_root(spec.root)
    rules = context.ports.path_rules
    walker = _Walker(spec, rules, context, action)
    tree = walker.walk(root)
    folders, files = _flatten(tree)
    counts = ScanCounts(
        folders=len(folders),
        video_files=sum(1 for media in files if media.kind == "video"),
        audio_files=sum(1 for media in files if media.kind == "audio"),
        subtitle_files=_subtitle_files(tree),
    )
    return ScanTreeResponse(
        scan_id=context.ports.ids.new_id(),
        root=root,
        path_flavor=rules.flavor,
        folders=tuple(folders),
        files=tuple(files),
        counts=counts,
        warnings=tuple(walker.reader.warnings),
    )
