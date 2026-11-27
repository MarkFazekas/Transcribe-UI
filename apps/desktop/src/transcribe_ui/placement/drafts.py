"""Target names of the placement strategies and their collision rules (architecture §3.5).

Names are worked out for every job of a download, whatever its subtitle state, so a partial
download names its files the same way as a later one (Q24, Q25).

Collisions (two names equal under the path-flavour rule in one target folder):

- flat strategies: each colliding file gets the names of the folders closest to it as a prefix
  (on top of the strategy's chosen folders), adding levels until files of different folders
  differ (`A_movie.hu.srt`, `B_movie.hu.srt`);
- files that still collide (same folder, e.g. `movie.mkv` and `movie.mp4`, in every strategy)
  each get their source extension added to the stem (`movie_mkv.hu.srt`, `movie_mp4.hu.srt`).

What collides after that (folder names that contain `_`, such as `A_B` and `A/B`) is a plan
error; nothing is numbered.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import PurePath

from transcribe_ui.placement.classes import PlacementJob, RenameReason
from transcribe_ui.placement.constants import NAME_JOINER
from transcribe_ui.placement.naming import PathKey, TargetNames, split_name, subtitle_name

# Rule keys of a target folder and a file name: equal keys are the same target.
type TargetKey = tuple[PathKey, str]


@dataclass(frozen=True)
class Source:
    """A job's media file split into the parts naming needs."""

    job: PlacementJob
    # The media file's folder.
    folder: PurePath
    stem: str
    # Without the dot; empty when the file has none.
    extension: str
    # Folder names from the scan root down to the file's folder; None when outside the root.
    relative: tuple[str, ...] | None

    @property
    def depth(self) -> int:
        """Return the number of folders between the scan root and the file (0 when outside it)."""
        return len(self.relative or ())


def source_of(job: PlacementJob, scan_root: PurePath, names: TargetNames) -> Source:
    """Return a job's source, with its folders below the scan root."""
    path = names.pure(job.source_path)
    root_key = names.key(scan_root)
    depth = len(root_key)
    inside = names.key(path.parent)[:depth] == root_key
    relative = path.parent.parts[depth:] if inside else None
    stem, extension = split_name(path.name)
    folder = path.parent
    return Source(job=job, folder=folder, stem=stem, extension=extension, relative=relative)


def closest_levels(depth: int, count: int) -> frozenset[int]:
    """Return the levels (1 = the first folder below the scan root) of the `count` folders closest to a file."""
    first = depth - min(count, depth) + 1
    return frozenset(range(first, depth + 1))


@dataclass
class Draft:
    """A job's target while its name is worked out."""

    source: Source
    # The target folder; None when the strategy cannot place this source (outside the scan root).
    folder: PurePath | None
    # Folder levels below the scan root (1 = the first) joined into the name by the strategy.
    levels: frozenset[int] = frozenset()
    # Folders closest to the file added to the name to tell it apart from other folders' files.
    closest: int = 0
    with_extension: bool = False
    renamed_by: list[RenameReason] = field(default_factory=list)

    def name(self, names: TargetNames) -> str:
        """Return the target file name."""
        relative = self.source.relative or ()
        chosen = self.levels | closest_levels(self.source.depth, self.closest)
        parts = [relative[level - 1] for level in sorted(chosen)]
        parts.append(self.source.stem)
        if self.with_extension:
            parts.append(self.source.extension)
        stem = NAME_JOINER.join(parts)
        return names.sanitised(subtitle_name(stem, self.source.job.language))

    def target(self, names: TargetNames) -> PurePath | None:
        """Return the target path, or None when there is none."""
        if self.folder is None:
            return None
        return self.folder / self.name(names)

    def key(self, names: TargetNames) -> TargetKey | None:
        """Return the key of the target, or None when there is none."""
        if self.folder is None:
            return None
        name_key = names.rules.key(self.name(names))
        return names.key(self.folder), name_key


def colliding(drafts: Iterable[Draft], names: TargetNames) -> list[list[Draft]]:
    """Return the groups of drafts with the same target, in input order."""
    groups: dict[TargetKey, list[Draft]] = {}
    for draft in drafts:
        key = draft.key(names)
        if key is not None:
            groups.setdefault(key, []).append(draft)
    return [group for group in groups.values() if len(group) > 1]


def resolve_collisions(drafts: Sequence[Draft], names: TargetNames, *, by_folders: bool) -> None:
    """Rename colliding drafts: by folder names (flat strategies), then by source extension."""
    for group in colliding(drafts, names):
        if by_folders:
            _add_folders(group, names)
        for same_folder in colliding(group, names):
            _add_extensions(same_folder)


def _add_extensions(drafts: Iterable[Draft]) -> None:
    for draft in drafts:
        if draft.source.extension:
            draft.with_extension = True
            draft.renamed_by.append("source_extension")


def _separated(group: Sequence[Draft], names: TargetNames) -> bool:
    # Whether no two drafts of different source folders share a name.
    folders_by_name: dict[str, set[PathKey]] = {}
    for draft in group:
        name_key = names.rules.key(draft.name(names))
        folders = folders_by_name.setdefault(name_key, set())
        folders.add(names.key(draft.source.folder))
    return all(len(folders) == 1 for folders in folders_by_name.values())


def _add_folders(group: Sequence[Draft], names: TargetNames) -> None:
    source_folders = {names.key(draft.source.folder) for draft in group}
    if len(source_folders) == 1:
        return
    before = [draft.name(names) for draft in group]
    deepest = max(draft.source.depth for draft in group)
    for closest in range(1, deepest + 1):
        for draft in group:
            draft.closest = closest
        if _separated(group, names):
            break
    for changed, name in zip(group, before, strict=True):
        if changed.name(names) != name:
            changed.renamed_by.append("folders")
