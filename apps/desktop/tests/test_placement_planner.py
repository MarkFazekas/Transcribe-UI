"""Placement planner: naming, collisions, outcomes and errors per strategy (architecture §3.5; plan m5 §3 M5.7).

Naming cases are written as comma-separated paths relative to the scan root (sources) and to
the root or the output folder (targets).
"""

from typing import Final

import pytest
from pydantic import ValidationError

from support.placement import (
    FLAT,
    FLAT_WITH_FOLDERS,
    MIRROR,
    MOVIE,
    MOVIE_TARGET,
    NEXT_TO_MEDIA,
    POSIX_OUTPUT,
    POSIX_ROOT,
    WINDOWS_OUTPUT,
    WINDOWS_ROOT,
    FakeProbe,
    Planning,
    below,
    error_codes,
    jobs_of,
    outcomes,
    plan_of,
    targets,
    windows_path,
)
from transcribe_ui.discovery.rules import macos_rules, posix_rules, windows_rules
from transcribe_ui.placement.classes import PlacementOptions, PlacementStrategy, SubtitleState
from transcribe_ui.placement.planner import plan_placement

# Option names.
LEVELS: Final = "folder_levels"
STRATEGY: Final = "strategy"

NAMING_CASES = (
    pytest.param(NEXT_TO_MEDIA, {}, MOVIE, "A/movie.hu.srt", id="next-to-media"),
    pytest.param(
        NEXT_TO_MEDIA,
        {},
        "A/movie.mkv,A/movie.mp4,B/movie.mkv",
        "A/movie_mkv.hu.srt,A/movie_mp4.hu.srt,B/movie.hu.srt",
        id="next-to-media-same-stem",
    ),
    pytest.param(NEXT_TO_MEDIA, {}, "A/Movie.mkv,A/movie.mp4", "A/Movie.hu.srt,A/movie.hu.srt", id="posix-case"),
    pytest.param(MIRROR, {}, "A/B/e.mkv", "A/B/e.hu.srt", id="mirror"),
    pytest.param(MIRROR, {}, "e.mkv", "e.hu.srt", id="mirror-root-file"),
    pytest.param(MIRROR, {}, "A/m.mkv,A/m.mp4", "A/m_mkv.hu.srt,A/m_mp4.hu.srt", id="mirror-same-stem"),
    pytest.param(
        FLAT,
        {},
        "A/movie.mkv,B/movie.mkv,C/other.mkv",
        "A_movie.hu.srt,B_movie.hu.srt,other.hu.srt",
        id="flat-prefix",
    ),
    pytest.param(FLAT, {}, "X/A/m.mkv,Y/A/m.mkv", "X_A_m.hu.srt,Y_A_m.hu.srt", id="flat-two-levels"),
    pytest.param(FLAT, {}, "m.mkv,A/m.mkv", "m.hu.srt,A_m.hu.srt", id="flat-root-file"),
    pytest.param(
        FLAT,
        {},
        "A/m.mkv,A/m.mp4,B/m.mkv",
        "A_m_mkv.hu.srt,A_m_mp4.hu.srt,B_m.hu.srt",
        id="flat-folders-then-extension",
    ),
    pytest.param(FLAT_WITH_FOLDERS, {"folder_count": 2}, "B/C/D/e.mkv", "C_D_e.hu.srt", id="closest-two"),
    pytest.param(FLAT_WITH_FOLDERS, {"folder_count": 10}, "B/C/D/e.mkv", "B_C_D_e.hu.srt", id="closest-past-depth"),
    pytest.param(FLAT_WITH_FOLDERS, {}, "e.mkv", "e.hu.srt", id="closest-root-file"),
    pytest.param(FLAT_WITH_FOLDERS, {LEVELS: [3, 1]}, "B/C/D/e.mkv", "B_D_e.hu.srt", id="levels"),
    pytest.param(
        FLAT_WITH_FOLDERS,
        {LEVELS: [1, 5]},
        "B/C/D/e.mkv,B/f.mkv",
        "B_e.hu.srt,B_f.hu.srt",
        id="levels-missing-skipped",
    ),
    pytest.param(FLAT_WITH_FOLDERS, {}, "A/C/e.mkv,B/C/e.mkv", "A_C_e.hu.srt,B_C_e.hu.srt", id="closest-collision"),
    pytest.param(
        FLAT,
        {},
        "A/movie.mkv,B/movie.mkv,A_movie.mp4",
        "A_movie_mkv.hu.srt,B_movie.hu.srt,A_movie_mp4.hu.srt",
        id="flat-rename-meets-root-file",
    ),
    pytest.param(
        NEXT_TO_MEDIA,
        {},
        "movie.mkv,movie.mp4,movie_mkv.avi",
        "movie_mkv.hu.srt,movie_mp4.hu.srt,movie_mkv_avi.hu.srt",
        id="extension-meets-stem",
    ),
    pytest.param(
        FLAT_WITH_FOLDERS,
        {LEVELS: [1]},
        "A/x/e.mkv,A/y/e.mkv",
        "A_x_e.hu.srt,A_y_e.hu.srt",
        id="levels-collision",
    ),
)

WINDOWS_CASES = (
    pytest.param(FLAT_WITH_FOLDERS, {"folder_count": 2}, "B/C/D/e.mkv", "C_D_e.hu.srt", id="requirements-example"),
    pytest.param(
        NEXT_TO_MEDIA,
        {},
        "x/Movie.mkv,x/movie.MP4",
        "x/Movie_mkv.hu.srt,x/movie_MP4.hu.srt",
        id="case-insensitive-same-stem",
    ),
    pytest.param(FLAT, {}, "x/Movie.mkv,Y/movie.mkv", "x_Movie.hu.srt,Y_movie.hu.srt", id="case-insensitive-flat"),
    pytest.param(NEXT_TO_MEDIA, {}, "CON.mkv", "CON_.hu.srt", id="reserved-name"),
    pytest.param(NEXT_TO_MEDIA, {}, "com0.mkv", "com0_.hu.srt", id="reserved-com0"),
    pytest.param(NEXT_TO_MEDIA, {}, "lpt1 .mkv", "lpt1 _.hu.srt", id="reserved-with-space"),
    pytest.param(MIRROR, {}, "b?c.mkv", "b_c.hu.srt", id="invalid-character"),
    pytest.param(MIRROR, {}, "x. /e.mkv", "x__/e.hu.srt", id="trailing-dot-space-folder"),
)


def _expected_below(strategy: PlacementStrategy, root: str, output: str, case: str) -> list[str]:
    return below(root if strategy == NEXT_TO_MEDIA else output, case)


@pytest.mark.parametrize((STRATEGY, "options", "sources", "expected"), NAMING_CASES)
def test_posix_naming(strategy: PlacementStrategy, options: dict[str, object], sources: str, expected: str) -> None:
    """Each strategy names and separates its targets as §3.5 says."""
    plan = plan_of(below(POSIX_ROOT, sources), Planning(strategy, options))
    assert targets(plan) == _expected_below(strategy, POSIX_ROOT, POSIX_OUTPUT, expected)
    assert not plan.errors


@pytest.mark.parametrize((STRATEGY, "options", "sources", "expected"), WINDOWS_CASES)
def test_windows_naming(strategy: PlacementStrategy, options: dict[str, object], sources: str, expected: str) -> None:
    """The Windows rules compare names case-insensitively and avoid names Windows refuses."""
    paths = [windows_path(path) for path in below(WINDOWS_ROOT, sources)]
    plan = plan_of(paths, Planning(strategy, options, windows_rules()))
    wanted = _expected_below(strategy, WINDOWS_ROOT, WINDOWS_OUTPUT, expected)
    assert targets(plan) == [windows_path(path) for path in wanted]
    assert not plan.errors


def test_windows_scan_root_compares_case_insensitively() -> None:
    """A source spelt in another case than the scan root is still below it."""
    plan = plan_of([windows_path("c:/a/B/e.mkv")], Planning(MIRROR, rules=windows_rules()))
    assert targets(plan) == [windows_path(f"{WINDOWS_OUTPUT}/B/e.hu.srt")]


def test_macos_compares_names_case_insensitively() -> None:
    """On macOS `Movie` and `movie` of one folder collide too."""
    plan = plan_of(below(POSIX_ROOT, "A/Movie.mkv,A/movie.mp4"), Planning(rules=macos_rules()))
    assert targets(plan) == below(POSIX_ROOT, "A/Movie_mkv.hu.srt,A/movie_mp4.hu.srt")


def test_rename_reasons_are_listed() -> None:
    """The plan says which names were changed and why; an unchanged name has no reason."""
    plan = plan_of(below(POSIX_ROOT, "m.mkv,A/m.mkv,A/m.mp4"), Planning(FLAT))
    assert [entry.renamed_by for entry in plan.entries] == [
        (),
        ("folders", "source_extension"),
        ("folders", "source_extension"),
    ]


def test_languages_of_one_file_do_not_collide() -> None:
    """Two languages of one file get two names."""
    source = f"{POSIX_ROOT}/a.mkv"
    jobs = [*jobs_of([source]), *jobs_of([source], language="en")]
    options = PlacementOptions(strategy=FLAT, output_folder=POSIX_OUTPUT)
    plan = plan_placement(jobs, POSIX_ROOT, options, posix_rules(), FakeProbe())
    assert targets(plan) == below(POSIX_OUTPUT, "a.hu.srt,a.en.srt")


def test_collision_left_after_renaming_is_an_error() -> None:
    """Folder names joined by `_` can still collide; that is an error, never a number."""
    plan = plan_of(below(POSIX_ROOT, "A_B/m.mkv,A/B/m.mkv,B/m.mkv"), Planning(FLAT))
    assert error_codes(plan) == ["duplicate_target", "duplicate_target"]
    assert "m.mkv (hu): another job has the same target" in plan.errors[0].message


def test_existing_targets_are_skipped_unless_overwrite() -> None:
    """An existing target is skipped by default and replaced with overwrite."""
    probe = FakeProbe(targets={MOVIE_TARGET: "file"})
    sources = below(POSIX_ROOT, MOVIE)
    assert outcomes(plan_of(sources, Planning(probe=probe))) == ["skip_existing"]
    overwrite = plan_of(sources, Planning(options={"overwrite": True}, probe=probe))
    assert outcomes(overwrite) == ["overwrite"]
    assert not overwrite.errors


def test_missing_original_folder_is_skipped() -> None:
    """Q23: a file whose folder is gone is skipped with its reason; the others are placed."""
    probe = FakeProbe(folders=frozenset((f"{POSIX_ROOT}/A",)))
    plan = plan_of(below(POSIX_ROOT, "A/a.mkv,B/b.mkv"), Planning(probe=probe))
    assert outcomes(plan) == ["place", "skip_folder_missing"]
    assert not plan.errors


def test_other_strategies_ignore_the_original_folder() -> None:
    """Only next-to-media needs the original folder."""
    probe = FakeProbe(folders=frozenset())
    assert outcomes(plan_of(below(POSIX_ROOT, MOVIE), Planning(MIRROR, probe=probe))) == ["place"]


@pytest.mark.parametrize(
    ("subtitle", "options", "outcome"),
    [
        ("pending", {}, "not_finished"),
        ("failed", {}, "skip_failed"),
        ("empty", {}, "skip_empty"),
        ("empty", {"include_empty": True}, "place"),
        ("available", {}, "place"),
    ],
)
def test_outcome_by_subtitle_state(subtitle: SubtitleState, options: dict[str, object], outcome: str) -> None:
    """Q24: jobs without a result are "not finished yet"; failed and empty ones are skipped."""
    jobs = jobs_of(below(POSIX_ROOT, MOVIE), subtitle=subtitle)
    plan = plan_placement(jobs, POSIX_ROOT, PlacementOptions.model_validate(options), posix_rules(), FakeProbe())
    assert outcomes(plan) == [outcome]


def test_unfinished_jobs_keep_their_names() -> None:
    """Names do not depend on which jobs are finished, so a later download names files the same way."""
    jobs = [*jobs_of(below(POSIX_ROOT, MOVIE), subtitle="pending"), *jobs_of(below(POSIX_ROOT, "B/movie.mkv"))]
    options = PlacementOptions(strategy=FLAT, output_folder=POSIX_OUTPUT)
    plan = plan_placement(jobs, POSIX_ROOT, options, posix_rules(), FakeProbe())
    assert targets(plan) == below(POSIX_OUTPUT, "A_movie.hu.srt,B_movie.hu.srt")
    assert outcomes(plan) == ["not_finished", "place"]


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({STRATEGY: MIRROR}, "mirror needs an output_folder"),
        ({STRATEGY: FLAT_WITH_FOLDERS, "output_folder": POSIX_OUTPUT, LEVELS: [1], "folder_count": 2}, "not both"),
        ({STRATEGY: FLAT, "output_folder": POSIX_OUTPUT, LEVELS: [1]}, "only to flat_with_folders"),
        ({STRATEGY: FLAT_WITH_FOLDERS, "output_folder": POSIX_OUTPUT, LEVELS: [1, 1]}, "more than once"),
        ({STRATEGY: FLAT_WITH_FOLDERS, "output_folder": POSIX_OUTPUT, LEVELS: [0]}, "greater than 0"),
    ],
)
def test_invalid_options(options: dict[str, object], message: str) -> None:
    """Options that do not fit their strategy are refused."""
    with pytest.raises(ValidationError, match=message):
        PlacementOptions.model_validate(options)
