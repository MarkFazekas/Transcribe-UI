"""The folder walk behind `scan_tree` and `preview_selection` (architecture §3.2).

`os.scandir` without following links (symlinks and Windows junctions are reported and skipped),
hidden and system entries left out unless asked, entries classified by extension; no ffprobe.
Names that are not valid Unicode cannot be shown or stored, so they are reported and skipped.
"""

import os
import stat
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
    ScanWarning,
    ScanWarningCode,
    Sidecar,
)
from transcribe_ui.discovery.constants import (
    ERROR_ROOT_NOT_A_FOLDER,
    ERROR_ROOT_NOT_ABSOLUTE,
    ERROR_ROOT_NOT_FOUND,
    HIDDEN_FILE_ATTRIBUTES,
    HIDDEN_NAME_PREFIX,
    MAX_SCAN_DEPTH,
    SCAN_PROGRESS_INTERVAL,
    SCAN_STAGE,
    SIDECAR_EXTENSIONS,
)
from transcribe_ui.discovery.rules import PathRules, display_order
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
    media: list[_Media] = field(default_factory=list)
    children: list["_Folder"] = field(default_factory=list)
    subtitle_files: int = 0

    def has_media(self) -> bool:
        # Children are kept only when they hold media themselves.
        return bool(self.media or self.children)


@dataclass(frozen=True)
class _Entry:
    name: str
    path: str
    entry_stat: os.stat_result


def _is_hidden(name: str, entry_stat: os.stat_result) -> bool:
    attributes = getattr(entry_stat, "st_file_attributes", 0)  # Windows only.
    flags = getattr(entry_stat, "st_flags", 0)  # macOS and BSD only.
    hidden_flag = bool(flags & stat.UF_HIDDEN)
    return name.startswith(HIDDEN_NAME_PREFIX) or bool(attributes & HIDDEN_FILE_ATTRIBUTES) or hidden_flag


def _is_valid_unicode(name: str) -> bool:
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _shown(name: str) -> str:
    """Return a name with what is not valid Unicode replaced by U+FFFD."""
    return os.fsencode(name).decode("utf-8", errors="replace")


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
    """Walks one tree; collects warnings and reports progress."""

    def __init__(self, spec: ScanSpec, rules: PathRules, context: RequestContext, action: str) -> None:
        video: dict[str, MediaKind] = dict.fromkeys(spec.video_extensions, "video")
        audio: dict[str, MediaKind] = dict.fromkeys(spec.audio_extensions, "audio")
        self._kinds = video | audio
        self._include_hidden = spec.include_hidden
        self._rules = rules
        self._context = context
        self._action = action
        self._found = 0
        self.warnings: list[ScanWarning] = []

    def folder(self, path: str, name: str, depth: int) -> _Folder:
        """Return the folder with its media files and its subfolders that hold media."""
        self._context.check_cancelled()
        node = _Folder(name, path)
        subtitles: list[NamedPath] = []
        for entry in self._entries(path):
            if stat.S_ISDIR(entry.entry_stat.st_mode):
                self._descend(node, entry, depth)
            elif stat.S_ISREG(entry.entry_stat.st_mode):
                self._classify(node, entry, subtitles)
        self._attach(node, subtitles)
        return node

    def _warn(self, code: ScanWarningCode, path: str, message: str) -> None:
        self.warnings.append(ScanWarning(code=code, path=path, message=message))

    def _entries(self, path: str) -> list[_Entry]:
        try:
            with os.scandir(path) as listing:
                found = sorted(listing, key=lambda entry: display_order(entry.name))
        except OSError:
            self._warn("unreadable", path, "The folder could not be read.")
            return []
        kept = (self._inspect(entry) for entry in found)
        return [entry for entry in kept if entry is not None]

    def _inspect(self, entry: os.DirEntry[str]) -> _Entry | None:
        if not _is_valid_unicode(entry.name):
            folder = Path(entry.path).parent
            shown = str(folder / _shown(entry.name))
            self._warn("invalid_name", shown, "The name is not valid Unicode; rename it to include it.")
            return None
        try:
            entry_stat = entry.stat(follow_symlinks=False)
            linked = entry.is_symlink() or entry.is_junction()
        except OSError:
            self._warn("unreadable", entry.path, "The entry could not be read.")
            return None
        if not self._include_hidden and _is_hidden(entry.name, entry_stat):
            return None
        if linked:
            self._warn("link_skipped", entry.path, "Links are not followed.")
            return None
        return _Entry(entry.name, entry.path, entry_stat)

    def _descend(self, node: _Folder, entry: _Entry, depth: int) -> None:
        if depth >= MAX_SCAN_DEPTH:
            self._warn("too_deep", entry.path, f"Folders deeper than {MAX_SCAN_DEPTH} levels are not scanned.")
            return
        child = self.folder(entry.path, entry.name, depth + 1)
        if child.has_media():
            node.children.append(child)

    def _classify(self, node: _Folder, entry: _Entry, subtitles: list[NamedPath]) -> None:
        extension = PurePath(entry.name).suffix[1:].lower()
        kind = self._kinds.get(extension)
        if kind is not None:
            entry_stat = entry.entry_stat
            media = _Media(entry.name, entry.path, kind, entry_stat.st_size, entry_stat.st_mtime_ns)
            node.media.append(media)
            self._count_media(node.path)
        elif extension in SIDECAR_EXTENSIONS:
            subtitles.append((entry.name, entry.path))

    def _count_media(self, path: str) -> None:
        self._found += 1
        if self._found % SCAN_PROGRESS_INTERVAL == 0:
            found = self._found
            event = ProgressEvent(action=self._action, stage=SCAN_STAGE, completed=found, path=path)
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


def scan(spec: ScanSpec, rules: PathRules, context: RequestContext, action: str) -> ScanTreeResponse:
    """Walk the tree below `spec.root`; progress events carry `action`.

    Raises:
        DomainError: If the root is not an absolute path of an existing folder.
    """
    root = checked_root(spec.root)
    walker = _Walker(spec, rules, context, action)
    name = Path(root).name or root
    tree = walker.folder(root, name, depth=0)
    folders, files = _flatten(tree)
    counts = ScanCounts(
        folders=len(folders),
        video_files=sum(1 for media in files if media.kind == "video"),
        audio_files=sum(1 for media in files if media.kind == "audio"),
        subtitle_files=_subtitle_files(tree),
    )
    return ScanTreeResponse(
        root=root,
        path_flavor=rules.flavor,
        folders=tuple(folders),
        files=tuple(files),
        counts=counts,
        warnings=tuple(walker.warnings),
    )
