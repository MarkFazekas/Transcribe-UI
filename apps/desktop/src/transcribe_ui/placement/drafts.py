"""Target names of the placement strategies and their collision rules (architecture §3.5).

Names are worked out for every job of a download, whatever its subtitle state, so a partial
download names its files the same way as a later one (requirements "Subtitle download options", Q25).

Collisions (two names equal under the path-flavour rule in one target folder):

- flat strategies: each colliding file gets the names of the folders closest to it as a prefix
  (on top of the strategy's chosen folders), adding levels until files of different folders
  differ (`A_movie.hu.srt`, `B_movie.hu.srt`);
- files that still collide (same folder, e.g. `movie.mkv` and `movie.mp4`, in every strategy)
  each get their source extension added to the stem (`movie_mkv.hu.srt`, `movie_mp4.hu.srt`).

The steps repeat, round by round, while a rename makes a new collision (`movie_mkv.hu.srt`
meeting the source `movie_mkv.avi`, or `A_movie.hu.srt` meeting a root file `A_movie.mp4`).
What still collides when no step changes a name is a plan error; nothing is numbered. Every
name then holds all its folders below the scan root, its stem and its extension joined by
`_`, so this needs a `_` inside one of those names (from the source, or from an invalid
character replaced): `A_B/m.mkv` and `A/B/m.mkv`, or a root `A_movie.mkv` and `A/movie.mkv`.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import PurePath

from transcribe_ui.placement.classes import PlacementJob, RenameReason
from transcribe_ui.placement.constants import MAX_COLLISION_ROUNDS, NAME_JOINER
from transcribe_ui.placement.naming import PathKey, TargetNames, is_clean_absolute, split_name, subtitle_name

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
    relative: tuple[str, ...] | None = None
    # Whether the source path is absolute without `..` parts (else nothing is placed for it).
    valid: bool = False

    @property
    def depth(self) -> int:
        """Return the number of folders between the scan root and the file (0 when outside it)."""
        return len(self.relative or ())


def source_of(job: PlacementJob, scan_root: PurePath, names: TargetNames) -> Source:
    """Return a job's source, with its folders below the scan root."""
    path = names.pure(job.source_path)
    folder = path.parent
    stem, extension = split_name(path.name)
    source = Source(job=job, folder=folder, stem=stem, extension=extension)
    if not is_clean_absolute(path):
        return source
    return replace(source, relative=_below(folder, scan_root, names), valid=True)


def _below(folder: PurePath, scan_root: PurePath, names: TargetNames) -> tuple[str, ...] | None:
    # The folder's parts below the scan root, or None when it is not at or below it.
    root_key = names.key(scan_root)
    depth = len(root_key)
    if names.key(folder)[:depth] != root_key:
        return None
    return folder.parts[depth:]


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
    """Rename colliding drafts until no rename helps: by folder names (flat strategies), then by source extension.

    Rounds repeat because a new name can collide with a draft outside its group (`A/movie.mkv`
    becomes `A_movie.hu.srt`, which a root file `A_movie.mkv` already has). Each round changes
    at least one draft's state, which only grows, so the rounds end.
    """
    rounds = 0
    while rounds < MAX_COLLISION_ROUNDS:
        groups = colliding(drafts, names)
        changed = [_separate(group, names, by_folders=by_folders) for group in groups]
        if not any(changed):
            return
        rounds += 1


def _separate(group: Sequence[Draft], names: TargetNames, *, by_folders: bool) -> bool:
    # One renaming step for a group of drafts with the same target; whether a draft changed.
    if by_folders and _add_folders(group, names):
        return True
    return _add_extensions(group)


def _add_extensions(drafts: Iterable[Draft]) -> bool:
    changed = False
    for draft in drafts:
        if draft.source.extension and not draft.with_extension:
            draft.with_extension = True
            draft.renamed_by.append("source_extension")
            changed = True
    return changed


def _separated(group: Sequence[Draft], names: TargetNames) -> bool:
    # Whether no two drafts of different source folders share a name.
    folders_by_name: dict[str, set[PathKey]] = {}
    for draft in group:
        name_key = names.rules.key(draft.name(names))
        folders = folders_by_name.setdefault(name_key, set())
        folders.add(names.key(draft.source.folder))
    return all(len(folders) == 1 for folders in folders_by_name.values())


def _add_folders(group: Sequence[Draft], names: TargetNames) -> bool:
    # Raise the closest-folder count of the group until files of different folders differ;
    # whether a name changed.
    source_folders = {names.key(draft.source.folder) for draft in group}
    if len(source_folders) == 1:
        return False
    before = [draft.name(names) for draft in group]
    starts = [draft.closest for draft in group]
    deepest = max(draft.source.depth for draft in group)
    for closest in range(1, deepest + 1):
        for draft, start in zip(group, starts, strict=True):
            draft.closest = max(start, closest)
        if _separated(group, names):
            break
    return _mark_folders(group, before, names)


def _mark_folders(group: Sequence[Draft], before: Sequence[str], names: TargetNames) -> bool:
    # Add the `folders` reason to each draft whose name changed; whether one did.
    renamed = False
    for draft, name in zip(group, before, strict=True):
        if draft.name(names) == name:
            continue
        renamed = True
        if "folders" not in draft.renamed_by:
            draft.renamed_by.append("folders")
    return renamed
