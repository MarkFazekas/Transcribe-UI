"""Resolution of the ordered include/exclude operations of `preview_selection` (architecture §3.2).

Each operation includes or excludes the files at or below its path (a folder or a file) that
match the filters active when it was made; later operations override earlier ones. Paths
compare under the host's path-flavour rule (case-insensitive on Windows and macOS, Unicode
normalisation-insensitive on macOS).
"""

from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from transcribe_ui.api.errors import DomainError
from transcribe_ui.discovery.classes import (
    FolderSelection,
    FolderState,
    PreviewSelectionResponse,
    ScanFile,
    ScanFolder,
    ScanTreeResponse,
    ScanWarning,
    SelectedFile,
    SelectionCounts,
    SelectionOperation,
)
from transcribe_ui.discovery.constants import ERROR_PATH_OUTSIDE_ROOT
from transcribe_ui.discovery.filters import FileFacts, compile_filter, facts_of
from transcribe_ui.discovery.rules import PathRules

# Rule keys of a path's parts below the scan root.
type RelativeKey = tuple[str, ...]


class _Index:
    """The scanned tree keyed for path matching."""

    def __init__(self, tree: ScanTreeResponse, rules: PathRules) -> None:
        self.tree = tree
        self.rules = rules
        self.root_key = self.key_of(rules.parts(tree.root))
        folder_keys: list[RelativeKey] = []
        for folder in tree.folders:
            folder_keys.append(self._folder_key(folder_keys, folder))
        self.file_keys = [self._file_key(folder_keys, scanned) for scanned in tree.files]
        self.facts = [facts_of(scanned) for scanned in tree.files]

    def key_of(self, parts: Sequence[str]) -> RelativeKey:
        """Return the rule keys of path parts."""
        return tuple(self.rules.key(part) for part in parts)

    def relative_key(self, path: str) -> RelativeKey:
        """Return the key of a path below the root.

        Raises:
            DomainError: If the path is relative, contains `..` or is not at or below the root.
        """
        parts = self.rules.parts(path)
        keys = self.key_of(parts)
        depth = len(self.root_key)
        inside = keys[:depth] == self.root_key and ".." not in parts
        if not inside or not Path(path).is_absolute():
            raise DomainError(ERROR_PATH_OUTSIDE_ROOT, "A selection path is not inside the scanned folder.")
        return keys[depth:]

    def below(self, prefix: RelativeKey) -> list[int]:
        """Return the positions of the files at or below a relative key."""
        size = len(prefix)
        positions = enumerate(self.file_keys)
        return [position for position, key in positions if key[:size] == prefix]

    def _folder_key(self, known: Sequence[RelativeKey], folder: ScanFolder) -> RelativeKey:
        if folder.parent is None:
            return ()
        return (*known[folder.parent], self.rules.key(folder.name))

    def _file_key(self, folder_keys: Sequence[RelativeKey], scanned: ScanFile) -> RelativeKey:
        return (*folder_keys[scanned.folder], self.rules.key(scanned.name))


def _apply(index: _Index, operation: SelectionOperation, selected: set[int]) -> ScanWarning | None:
    prefix = index.relative_key(operation.path)
    below = index.below(prefix)
    if not below:
        message = "No media file is at or below this path."
        return ScanWarning(code="no_media_at_path", path=operation.path, message=message)
    matches = compile_filter(operation.filters)
    hits = {position for position in below if matches(index.facts[position])}
    if operation.operation == "include":
        selected.update(hits)
    else:
        selected.difference_update(hits)
    return None


def _selected_file(index: _Index, position: int) -> SelectedFile:
    scanned = index.tree.files[position]
    facts: FileFacts = index.facts[position]
    forced_sidecars = (sidecar for sidecar in scanned.subtitles if sidecar.forced)
    forced = {sidecar.language for sidecar in forced_sidecars if sidecar.language is not None}
    return SelectedFile(
        name=scanned.name,
        path=scanned.path,
        kind=scanned.kind,
        size_bytes=scanned.size_bytes,
        subtitle_languages=tuple(sorted(facts.languages)),
        forced_subtitle_languages=tuple(sorted(forced)),
        unknown_language_subtitles=sum(1 for sidecar in scanned.subtitles if sidecar.language is None),
    )


def _folder_states(tree: ScanTreeResponse, selected: set[int]) -> tuple[FolderSelection, ...]:
    totals: Counter[int] = Counter()
    chosen: Counter[int] = Counter()
    for position, scanned in enumerate(tree.files):
        folder: int | None = scanned.folder
        while folder is not None:
            totals[folder] += 1
            chosen[folder] += int(position in selected)
            folder = tree.folders[folder].parent
    states: list[FolderSelection] = []
    for number, folder_found in enumerate(tree.folders):
        if chosen[number]:
            state: FolderState = "all" if chosen[number] == totals[number] else "partial"
            states.append(FolderSelection(path=folder_found.path, state=state))
    return tuple(states)


def select(
    tree: ScanTreeResponse, operations: Sequence[SelectionOperation], rules: PathRules
) -> PreviewSelectionResponse:
    """Apply the operations in order to the scanned tree.

    Raises:
        DomainError: If an operation's path is not inside the scanned folder.
    """
    index = _Index(tree, rules)
    selected: set[int] = set()
    warnings = list(tree.warnings)
    for operation in operations:
        warning = _apply(index, operation, selected)
        if warning is not None:
            warnings.append(warning)
    files = tuple(_selected_file(index, position) for position in sorted(selected))
    counts = SelectionCounts(
        files=len(files),
        video_files=sum(1 for chosen in files if chosen.kind == "video"),
        audio_files=sum(1 for chosen in files if chosen.kind == "audio"),
        size_bytes=sum(chosen.size_bytes for chosen in files),
    )
    return PreviewSelectionResponse(
        root=tree.root,
        path_flavor=tree.path_flavor,
        files=files,
        folders=_folder_states(tree, selected),
        counts=counts,
        warnings=tuple(warnings),
    )
