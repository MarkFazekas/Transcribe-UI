"""Builders and a fake file-system probe for the placement planner tests (plan m5 §3 M5.7).

Paths are written with `/` in the tests; `windows_path` turns them into Windows paths, so the
Windows rules are tested on any OS.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from transcribe_ui.discovery.rules import PathRules, posix_rules
from transcribe_ui.placement.classes import (
    PlacementJob,
    PlacementOptions,
    PlacementPlan,
    PlacementStrategy,
    SubtitleState,
)
from transcribe_ui.placement.planner import plan_placement
from transcribe_ui.placement.probe import TargetKind

# Strategy names.
NEXT_TO_MEDIA: Final = "next_to_media"
MIRROR: Final = "mirror"
FLAT: Final = "flat"
FLAT_WITH_FOLDERS: Final = "flat_with_folders"

# Prefix of the test item ids; a counter fills the rest (valid Crockford base32).
ITEM_ID_PREFIX = "01J"
# Length of a ULID.
ULID_LENGTH = 26
# Language of the test jobs unless a test says otherwise.
LANGUAGE = "hu"
# Scan root of the POSIX cases.
POSIX_ROOT = "/media"
# Output folder of the POSIX cases.
POSIX_OUTPUT = "/out"
# Scan root of the Windows cases (written with `/`).
WINDOWS_ROOT = "C:/A"
# Output folder of the Windows cases (written with `/`).
WINDOWS_OUTPUT = "D:/subs"
# The common source of the single-file cases (below the POSIX root).
MOVIE = "A/movie.mkv"
# Its target next to it.
MOVIE_TARGET = f"{POSIX_ROOT}/A/movie.hu.srt"
# Separates the paths of a case written as one string.
CASE_SEPARATOR = ","


def windows_path(path: str) -> str:
    """Return a path written with `/` as a Windows path."""
    return path.replace("/", "\N{REVERSE SOLIDUS}")


def item_id(number: int) -> str:
    """Return the test item id `number`."""
    return f"{ITEM_ID_PREFIX}{number:0{ULID_LENGTH - len(ITEM_ID_PREFIX)}d}"


def jobs_of(
    paths: Sequence[str], language: str = LANGUAGE, subtitle: SubtitleState = "available"
) -> list[PlacementJob]:
    """Return one job per source path, numbered in order."""
    return [
        PlacementJob(item_id=item_id(number), language=language, source_path=path, subtitle=subtitle)
        for number, path in enumerate(paths)
    ]


def below(folder: str, case: str) -> list[str]:
    """Return the comma-separated relative paths of a case below a folder."""
    return [f"{folder}/{path}" for path in case.split(CASE_SEPARATOR)]


@dataclass
class FakeProbe:
    """A file system described by data: existing folders, targets and symbolic links."""

    # Existing folders; None means every folder exists.
    folders: frozenset[str] | None = None
    # What is at each existing target path.
    targets: Mapping[str, TargetKind] = field(default_factory=dict)
    # Symbolic links: a path prefix and where it leads.
    links: Mapping[str, str] = field(default_factory=dict)

    def folder_exists(self, path: str) -> bool:
        """Return whether the folder is listed (or every folder exists)."""
        return self.folders is None or path in self.folders

    def target_kind(self, path: str) -> TargetKind:
        """Return what the data puts at the path."""
        return self.targets.get(path, "missing")

    def resolve(self, path: str) -> str:
        """Return the path with the first matching link prefix replaced."""
        for link, destination in self.links.items():
            if path.startswith(link):
                return destination + path.removeprefix(link)
        return path


@dataclass(frozen=True)
class Planning:
    """How `plan_of` plans: strategy, options, rules and file system."""

    strategy: PlacementStrategy = NEXT_TO_MEDIA
    options: Mapping[str, object] = field(default_factory=dict)
    rules: PathRules = field(default_factory=posix_rules)
    probe: FakeProbe = field(default_factory=FakeProbe)


def plan_of(paths: Sequence[str], planning: Planning | None = None) -> PlacementPlan:
    """Plan available jobs of the paths below the POSIX or Windows test root."""
    chosen = planning or Planning()
    windows = chosen.rules.flavor == "windows"
    fields: dict[str, object] = {"strategy": chosen.strategy, **chosen.options}
    if chosen.strategy != NEXT_TO_MEDIA:
        fields["output_folder"] = windows_path(WINDOWS_OUTPUT) if windows else POSIX_OUTPUT
    settings = PlacementOptions.model_validate(fields)
    root = windows_path(WINDOWS_ROOT) if windows else POSIX_ROOT
    return plan_placement(jobs_of(paths), root, settings, chosen.rules, chosen.probe)


def targets(plan: PlacementPlan) -> list[str | None]:
    """Return the plan's target paths in order."""
    return [entry.target_path for entry in plan.entries]


def outcomes(plan: PlacementPlan) -> list[str]:
    """Return the plan's outcomes in order."""
    return [entry.outcome for entry in plan.entries]


def error_codes(plan: PlacementPlan) -> list[str]:
    """Return the plan's error codes in order."""
    return [error.code for error in plan.errors]


def only_error(plan: PlacementPlan) -> str | None:
    """Return the code of the plan's only error, or None when it has none or several."""
    codes = error_codes(plan)
    return codes[0] if len(codes) == 1 else None


def has_error(plan: PlacementPlan, code: str, message: str) -> bool:
    """Return whether the plan has an error with the code whose message contains the text."""
    return any(message in error.message for error in plan.errors if error.code == code)
