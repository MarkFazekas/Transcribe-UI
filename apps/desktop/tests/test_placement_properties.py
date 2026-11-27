"""Seeded property checks of the placement planner (architecture §3.5; plan m5 §3 M5.7).

Random trees, strategies, subtitle states and existing targets, for the POSIX, macOS and
Windows rules (paths written with `/`, which the Windows rule also splits on).
"""

import random
from dataclasses import dataclass, field
from typing import Final

import pytest

from support.placement import (
    FLAT,
    FLAT_WITH_FOLDERS,
    MIRROR,
    NEXT_TO_MEDIA,
    POSIX_OUTPUT,
    POSIX_ROOT,
    WINDOWS_OUTPUT,
    WINDOWS_ROOT,
    FakeProbe,
    error_codes,
    item_id,
)
from transcribe_ui.discovery.rules import PathRules, macos_rules, posix_rules, windows_rules
from transcribe_ui.placement.classes import PlacementJob, PlacementOptions, PlacementPlan, SubtitleState
from transcribe_ui.placement.constants import WRITING_OUTCOMES
from transcribe_ui.placement.naming import PathKey, TargetNames
from transcribe_ui.placement.planner import plan_placement
from transcribe_ui.placement.probe import TargetKind

# Random plans per rule set.
ROUNDS = 300
# Most jobs of one random plan.
MAX_JOBS = 12
# Deepest random folder level below the scan root.
MAX_DEPTH = 4
# Seed of the generator.
SEED = 57
# Chance of a random flag being set.
CHANCE = 0.5
# Option names.
STRATEGY: Final = "strategy"
LEVELS: Final = "folder_levels"
# Subtitle states drawn for random jobs (available twice as often).
STATES: tuple[SubtitleState, ...] = ("available", "available", "pending", "empty", "failed")
# Names that make collisions likely, including `_` (which can leave a collision no rename resolves).
COLLIDING_FOLDERS = ("A", "a", "B", "A_B", "x")
COLLIDING_STEMS = ("movie", "Movie", "A_movie", "x")
COLLIDING_EXTENSIONS = ("mkv", "mp4", "MKV")
# Names without `_`, from disjoint vocabularies (folder, stem, extension), so every collision
# can be resolved.
CLEAN_FOLDERS = ("A", "a", "Bb", "Cc")
CLEAN_STEMS = ("movie", "Movie", "film")
CLEAN_EXTENSIONS = ("mkv", "mp4", "MKV", "avi")
# The rule sets with their scan root and output folder.
RULE_SETS = (
    pytest.param(posix_rules(), POSIX_ROOT, POSIX_OUTPUT, id="posix"),
    pytest.param(macos_rules(), POSIX_ROOT, POSIX_OUTPUT, id="macos"),
    pytest.param(windows_rules(), WINDOWS_ROOT, WINDOWS_OUTPUT, id="windows"),
)


@dataclass(frozen=True)
class Vocabulary:
    """The names random sources are made of."""

    folders: tuple[str, ...]
    stems: tuple[str, ...]
    extensions: tuple[str, ...]


@dataclass
class RandomProbe(FakeProbe):
    """Existing targets drawn at random (the same path always gets the same answer)."""

    generator: random.Random = field(default_factory=random.Random)
    kinds: tuple[TargetKind, ...] = ("missing",)
    drawn: dict[str, TargetKind] = field(default_factory=dict)

    def target_kind(self, path: str) -> TargetKind:
        """Return the kind drawn for the path the first time it was asked for."""
        return self.drawn.setdefault(path, self.generator.choice(self.kinds))


def _random_jobs(generator: random.Random, root: str, words: Vocabulary) -> list[PlacementJob]:
    jobs: list[PlacementJob] = []
    for number in range(generator.randrange(1, MAX_JOBS)):
        folders = [generator.choice(words.folders) for _level in range(generator.randrange(MAX_DEPTH))]
        name = f"{generator.choice(words.stems)}.{generator.choice(words.extensions)}"
        path = "/".join([root, *folders, name])
        language = generator.choice(["hu", "en"])
        state = generator.choice(STATES)
        jobs.append(PlacementJob(item_id=item_id(number), language=language, source_path=path, subtitle=state))
    return jobs


def _distinct(jobs: list[PlacementJob], names: TargetNames) -> list[PlacementJob]:
    # One job per source and language, sources compared under the rules.
    unique: dict[tuple[PathKey, str], PlacementJob] = {}
    for job in jobs:
        unique.setdefault((names.path_key(job.source_path), job.language), job)
    return list(unique.values())


def _random_options(generator: random.Random, output: str) -> PlacementOptions:
    strategy = generator.choice([NEXT_TO_MEDIA, MIRROR, FLAT, FLAT_WITH_FOLDERS])
    fields: dict[str, object] = {STRATEGY: strategy, "include_empty": generator.random() < CHANCE}
    fields["overwrite"] = generator.random() < CHANCE
    if strategy != NEXT_TO_MEDIA:
        fields["output_folder"] = output
    if strategy == FLAT_WITH_FOLDERS and generator.random() < CHANCE:
        fields[LEVELS] = sorted(generator.sample(range(1, MAX_DEPTH + 1), generator.randrange(1, 3)))
    elif strategy == FLAT_WITH_FOLDERS:
        fields["folder_count"] = generator.randrange(1, MAX_DEPTH)
    return PlacementOptions.model_validate(fields)


def _random_plan(
    generator: random.Random, rules: PathRules, folders: tuple[str, str], words: Vocabulary
) -> PlacementPlan:
    root, output = folders
    names = TargetNames(rules)
    jobs = _distinct(_random_jobs(generator, root, words), names)
    options = _random_options(generator, output)
    # A folder in the way is an error only when overwriting it, so it is drawn only without overwrite.
    kinds: tuple[TargetKind, ...] = ("missing", "file") if options.overwrite else ("missing", "file", "other")
    probe = RandomProbe(generator=generator, kinds=kinds)
    return plan_placement(jobs, root, options, rules, probe)


def _written_keys(plan: PlacementPlan, rules: PathRules) -> list[PathKey]:
    names = TargetNames(rules)
    written = [entry.target_path for entry in plan.entries if entry.outcome in WRITING_OUTCOMES]
    return [names.path_key(target) for target in written if target is not None]


@pytest.mark.parametrize(("rules", "root", "output"), RULE_SETS)
def test_valid_plan_never_writes_a_target_twice(rules: PathRules, root: str, output: str) -> None:
    """Whatever the tree, a plan without errors writes distinct targets; the only error is a collision left."""
    generator = random.Random(SEED)  # noqa: S311 -- reproducible test data, not cryptography.
    words = Vocabulary(COLLIDING_FOLDERS, COLLIDING_STEMS, COLLIDING_EXTENSIONS)
    for _ in range(ROUNDS):
        plan = _random_plan(generator, rules, (root, output), words)
        assert set(error_codes(plan)) <= {"duplicate_target"}
        keys = _written_keys(plan, rules)
        assert plan.errors or len(set(keys)) == len(keys)


@pytest.mark.parametrize(("rules", "root", "output"), RULE_SETS)
def test_distinct_clean_sources_always_plan(rules: PathRules, root: str, output: str) -> None:
    """Distinct sources whose names contain no `_` always give a plan without errors, whatever their states."""
    generator = random.Random(SEED)  # noqa: S311 -- reproducible test data, not cryptography.
    words = Vocabulary(CLEAN_FOLDERS, CLEAN_STEMS, CLEAN_EXTENSIONS)
    renamed = 0
    for _ in range(ROUNDS):
        plan = _random_plan(generator, rules, (root, output), words)
        assert not plan.errors, plan.errors
        all_targets = [entry.target_path for entry in plan.entries]
        keys = {TargetNames(rules).path_key(target) for target in all_targets if target is not None}
        assert len(keys) == len(all_targets)
        renamed += sum(bool(entry.renamed_by) for entry in plan.entries)
    assert renamed > ROUNDS
