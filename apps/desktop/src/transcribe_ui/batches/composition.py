"""A batch's items from a selection: scan (or the kept scan), select, assign target languages."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from transcribe_contracts.classes import PathFlavor
from transcribe_ui.api.classes import Confirmation
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.classes import BatchComposition
from transcribe_ui.batches.constants import RELATIVE_SEPARATOR
from transcribe_ui.batches.errors import NothingSelectedError, PathNotSelectedError
from transcribe_ui.batches.payloads import ItemPayload
from transcribe_ui.discovery.classes import ScanTreeResponse, ScanWarning, SelectedFile
from transcribe_ui.discovery.rules import PathRules
from transcribe_ui.discovery.scan import scan
from transcribe_ui.discovery.selection import select

if TYPE_CHECKING:
    from pydantic import JsonValue


# Target languages of a file.
type Languages = tuple[str, ...]


@dataclass(frozen=True)
class NewItem:
    """A selected file with its target languages, ready to be stored."""

    payload: ItemPayload
    languages: tuple[str, ...]

    def existing_languages(self) -> tuple[str, ...]:
        """Return the target languages the file already has a (not forced) sidecar subtitle in."""
        have = frozenset(self.payload.subtitle_languages)
        return tuple(language for language in self.languages if language in have)


@dataclass(frozen=True)
class Composition:
    """What a selection resolves to."""

    scan_root: str
    path_flavor: PathFlavor
    members: tuple[NewItem, ...]
    warnings: tuple[ScanWarning, ...]

    def confirmation(self) -> Confirmation | None:
        """Return the confirmation of jobs in languages the files already have subtitles in, or None."""
        files: list[JsonValue] = []
        for member in self.members:
            existing = member.existing_languages()
            if existing:
                files.append({"relative_path": member.payload.relative_path, "languages": list(existing)})
        if not files:
            return None
        message = f"{len(files)} selected file(s) already have a subtitle in a target language."
        return Confirmation(field="confirm_existing_languages", set_to=True, message=message, details={"files": files})


def _tree(request: BatchComposition, context: RequestContext, action: str) -> ScanTreeResponse:
    cache = context.ports.scan_cache
    scan_id = request.scan_id
    tree = None if scan_id is None else cache.get(scan_id, request.scan)
    if tree is None:
        tree = scan(request.scan, context, action)
        cache.put(request.scan, tree)
    return tree


def _relative(rules: PathRules, root: str, path: str) -> str:
    depth = len(rules.parts(root))
    return RELATIVE_SEPARATOR.join(rules.parts(path)[depth:])


def _path_key(rules: PathRules, path: str) -> tuple[str, ...]:
    """Return the form under which the file system considers two spellings of a path the same file."""
    return tuple(rules.key(part) for part in rules.parts(path))


def _languages(request: BatchComposition, rules: PathRules, paths: list[str]) -> dict[str, Languages]:
    """Return the target languages of each selected path: its `item_languages` entry, else the batch's.

    Entry paths are compared with the path-flavour rule, so both separators (Windows) and the
    case rules of the file system apply; of two spellings of one file, the later entry wins.

    Raises:
        PathNotSelectedError: If an entry names a file that is not selected.
    """
    by_key = {_path_key(rules, path): path for path in paths}
    languages = dict.fromkeys(paths, request.languages)
    for index, entry in enumerate(request.item_languages):
        selected = by_key.get(_path_key(rules, entry.path))
        if selected is None:
            raise PathNotSelectedError(index)
        languages[selected] = entry.languages
    return languages


def _members(
    tree: ScanTreeResponse,
    selected_files: tuple[SelectedFile, ...],
    rules: PathRules,
    languages: Mapping[str, Languages],
) -> tuple[NewItem, ...]:
    mtime_by_path = {scanned.path: scanned.mtime_ns for scanned in tree.files}
    members = []
    for position, selected in enumerate(selected_files):
        path = selected.path
        relative = _relative(rules, tree.root, path)
        payload = _payload(position, selected, mtime_by_path[path], relative)
        members.append(NewItem(payload, languages[path]))
    return tuple(members)


def compose(request: BatchComposition, context: RequestContext, action: str) -> Composition:
    """Resolve the request's selection into the batch's items.

    Raises:
        NothingSelectedError: If the selection selects no media file.
        PathNotSelectedError: If an `item_languages` entry names a file that is not selected.
    """
    tree = _tree(request, context, action)
    preview = select(tree, request.operations, context)
    if not preview.files:
        raise NothingSelectedError
    rules = context.ports.path_rules
    languages = _languages(request, rules, [selected.path for selected in preview.files])
    members = _members(tree, preview.files, rules, languages)
    return Composition(tree.root, rules.flavor, members, preview.warnings)


def _payload(position: int, selected: SelectedFile, mtime_ns: int, relative_path: str) -> ItemPayload:
    return ItemPayload(
        position=position,
        path=selected.path,
        relative_path=relative_path,
        name=selected.name,
        kind=selected.kind,
        size_bytes=selected.size_bytes,
        mtime_ns=mtime_ns,
        subtitle_languages=selected.subtitle_languages,
    )
