"""Placement plan errors: names, paths, sources, targets and options (architecture §3.5; plan m5 §3 M5.7)."""

import pytest

from support.placement import (
    FLAT,
    MIRROR,
    MOVIE,
    MOVIE_TARGET,
    NEXT_TO_MEDIA,
    POSIX_OUTPUT,
    POSIX_ROOT,
    WINDOWS_ROOT,
    FakeProbe,
    Planning,
    below,
    error_codes,
    jobs_of,
    only_error,
    outcomes,
    plan_of,
    targets,
    windows_path,
)
from transcribe_ui.discovery.rules import posix_rules, windows_rules
from transcribe_ui.placement.classes import PlacementOptions, PlacementStrategy
from transcribe_ui.placement.constants import (
    MAX_NAME_LENGTH,
    TEMP_TAIL_LENGTH,
    WINDOWS_MAX_FOLDER_LENGTH,
    WINDOWS_MAX_PATH_LENGTH,
)
from transcribe_ui.placement.naming import TargetNames
from transcribe_ui.placement.planner import plan_placement

# A file name of this many UTF-8 bytes plus `.hu.srt` exceeds 255 bytes.
LONG_STEM_LENGTH = 250
# Characters of `é` whose UTF-8 form (two bytes each) is too long while their UTF-16 form fits.
WIDE_STEM_LENGTH = 128
# Folder name length that makes a Windows target path longer than MAX_PATH.
LONG_FOLDER_LENGTH = 200
# A stem whose `.hu.srt` name fits in 255 bytes but not with the temp file's tail added.
TEMP_TIGHT_STEM_LENGTH = 245
# The Windows path separator.
BACKSLASH = "\N{REVERSE SOLIDUS}"


def test_name_too_long_is_an_error() -> None:
    """A name over 255 bytes is a plan error, not a truncation."""
    too_long = plan_of([f"{POSIX_ROOT}/{'x' * LONG_STEM_LENGTH}.mkv"])
    assert only_error(too_long) == "name_too_long"


def test_name_length_leaves_room_for_the_temp_suffix() -> None:
    """A name that fits only without the temp file's tail (`.<token>.tmp`) is too long."""
    stem = "x" * TEMP_TIGHT_STEM_LENGTH
    assert len(f"{stem}.hu.srt") <= MAX_NAME_LENGTH
    assert error_codes(plan_of([f"{POSIX_ROOT}/{stem}.mkv"])) == ["name_too_long"]


def test_windows_path_keeps_room_for_the_temp_file() -> None:
    """A Windows target fits when its temp file's path (`.<token>.tmp` added) fits MAX_PATH; folders over 247 never do."""
    names = TargetNames(windows_rules())
    name = f"{BACKSLASH}a.srt"
    fitting = WINDOWS_MAX_PATH_LENGTH - TEMP_TAIL_LENGTH - len(name)
    folder = windows_path(f"D:/{'f' * (fitting - len('D:/'))}")
    assert not names.path_too_long(f"{folder}{name}")
    assert names.path_too_long(f"{folder}f{name}")
    assert len(folder) < WINDOWS_MAX_FOLDER_LENGTH


def test_name_length_units_per_os() -> None:
    """Two-byte characters count twice on POSIX, once on Windows."""
    stem = "é" * WIDE_STEM_LENGTH
    assert error_codes(plan_of([f"{POSIX_ROOT}/{stem}.mkv"])) == ["name_too_long"]
    windows = plan_of([windows_path(f"{WINDOWS_ROOT}/{stem}.mkv")], Planning(rules=windows_rules()))
    assert not windows.errors


def test_windows_path_too_long_is_an_error() -> None:
    """A Windows target path over MAX_PATH is a plan error."""
    folder = "f" * LONG_FOLDER_LENGTH
    source = windows_path(f"{WINDOWS_ROOT}/{folder}/{folder}.mkv")
    too_long = plan_of([source], Planning(MIRROR, rules=windows_rules()))
    assert only_error(too_long) == "path_too_long"


def test_folder_in_the_way() -> None:
    """A folder at the target is skipped; overwriting it is an error."""
    probe = FakeProbe(targets={MOVIE_TARGET: "other"})
    sources = below(POSIX_ROOT, MOVIE)
    assert outcomes(plan_of(sources, Planning(probe=probe))) == ["skip_existing"]
    plan = plan_of(sources, Planning(options={"overwrite": True}, probe=probe))
    assert only_error(plan) == "target_not_a_file"


def test_name_errors_apply_to_unfinished_jobs() -> None:
    """Names are fixed per job (requirements "Subtitle download options"), so a name that cannot be written is an error before the result exists."""
    jobs = jobs_of([f"{POSIX_ROOT}/{'x' * LONG_STEM_LENGTH}.mkv"], subtitle="pending")
    plan = plan_placement(jobs, POSIX_ROOT, PlacementOptions(), posix_rules(), FakeProbe())
    assert only_error(plan) == "name_too_long"


def test_existing_target_matters_only_when_writing() -> None:
    """A folder in the way of an unfinished job is no error yet: the target is checked when it is written."""
    jobs = jobs_of(below(POSIX_ROOT, MOVIE), subtitle="pending")
    probe = FakeProbe(targets={MOVIE_TARGET: "other"})
    plan = plan_placement(jobs, POSIX_ROOT, PlacementOptions(overwrite=True), posix_rules(), probe)
    assert not plan.errors


def test_duplicate_target_whatever_the_outcome() -> None:
    """Two jobs on one target collide even while one of them has no subtitle yet."""
    jobs = jobs_of(below(POSIX_ROOT, "A_B/m.mkv,A/B/m.mkv,B/m.mkv"))
    jobs[0] = jobs[0].model_copy(update={"subtitle": "pending"})
    options = PlacementOptions(strategy=FLAT, output_folder=POSIX_OUTPUT)
    plan = plan_placement(jobs, POSIX_ROOT, options, posix_rules(), FakeProbe())
    assert outcomes(plan) == ["not_finished", "place", "place"]
    assert error_codes(plan) == ["duplicate_target", "duplicate_target"]
    assert [error.item_id for error in plan.errors] == [jobs[0].item_id, jobs[1].item_id]


def test_unreadable_target_is_an_error() -> None:
    """A target that cannot be checked is an error, never a skip."""
    probe = FakeProbe(targets={MOVIE_TARGET: "unreadable"})
    plan = plan_of(below(POSIX_ROOT, MOVIE), Planning(probe=probe))
    assert only_error(plan) == "target_unreadable"


@pytest.mark.parametrize(
    "source",
    ["media/A/a.mkv", f"{POSIX_ROOT}/../etc/a.mkv", f"{POSIX_ROOT}/A/../../x/a.mkv"],
    ids=["relative", "dot-dot-outside", "dot-dot-inside"],
)
@pytest.mark.parametrize("strategy", [NEXT_TO_MEDIA, MIRROR, FLAT])
def test_invalid_source_path_is_an_error(source: str, strategy: PlacementStrategy) -> None:
    """A relative source or one with `..` is refused; `..` never lets a target leave its folder."""
    plan = plan_of([source], Planning(strategy))
    assert targets(plan) == [None]
    assert only_error(plan) == "source_path_invalid"


@pytest.mark.parametrize("root", ["media", f"{POSIX_ROOT}/../media"], ids=["relative", "dot-dot"])
def test_invalid_scan_root_is_an_error(root: str) -> None:
    """The scan root must be absolute without `..`."""
    plan = plan_placement(jobs_of(below(POSIX_ROOT, MOVIE)), root, PlacementOptions(), posix_rules(), FakeProbe())
    assert only_error(plan) == "scan_root_invalid"


def test_next_to_media_ignores_an_output_folder() -> None:
    """Settings pre-filled with an output folder still work next to the media."""
    options = PlacementOptions(output_folder="relative")
    plan = plan_placement(jobs_of(below(POSIX_ROOT, MOVIE)), POSIX_ROOT, options, posix_rules(), FakeProbe())
    assert targets(plan) == [MOVIE_TARGET]
    assert not plan.errors


def test_source_outside_scan_root() -> None:
    """Mirror cannot place a file outside the scan root; flat can."""
    mirror = plan_of(["/elsewhere/a.mkv"], Planning(MIRROR))
    assert targets(mirror) == [None]
    assert error_codes(mirror) == ["outside_scan_root"]
    assert targets(plan_of(["/elsewhere/a.mkv"], Planning(FLAT))) == [f"{POSIX_OUTPUT}/a.hu.srt"]


def test_relative_output_folder_is_an_error() -> None:
    """The output folder must be absolute."""
    options = PlacementOptions(strategy=FLAT, output_folder="out")
    plan = plan_placement(jobs_of(below(POSIX_ROOT, MOVIE)), POSIX_ROOT, options, posix_rules(), FakeProbe())
    assert only_error(plan) == "output_not_absolute"
