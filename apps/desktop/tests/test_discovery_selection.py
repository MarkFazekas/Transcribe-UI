"""Flow tests of `preview_selection` (architecture §3.2; plan m5 §3 M5.3).

Each request runs through the backend and the CLI (parity); golden files are regenerated with
`UPDATE_SNAPSHOTS=1 uv run pytest apps/desktop/tests`.
"""

import dataclasses
import time
from pathlib import Path

import pytest

from support.discovery import (
    EPISODE_ONE,
    EPISODE_TWO,
    EVERY_FILE,
    EXTRAS,
    FESTIVE,
    FILM,
    FILM_SEQUEL,
    FILM_TWIN,
    INTERVIEW,
    MEDIA_ROOT,
    PODCAST,
    JsonObject,
    build_media_tree,
    error_code,
    exclude,
    file_names,
    include,
    media_dir,
    name_filter,
    preview_request,
    response_of,
    subtitles,
    warning_codes,
)
from support.fakes import seeded_ports
from support.golden import assert_golden
from support.parity import run_one
from support.trees import build_tree
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import ErrorResponse
from transcribe_ui.cli.constants import EXIT_ERROR, EXIT_INVALID_REQUEST, EXIT_OK
from transcribe_ui.discovery.classes import PreviewSelectionRequest, PreviewSelectionResponse
from transcribe_ui.discovery.rules import PathRules, macos_rules, posix_rules, windows_rules

SERIES = f"{MEDIA_ROOT}/Series"
MOVIES = f"{MEDIA_ROOT}/Movies"
AUDIO = f"{MEDIA_ROOT}/Audio"
HUNGARIAN = "hu"
MISSING_ONE = "missing_one"
NO_MEDIA_AT_PATH = "no_media_at_path"
# Folders x files per folder of the large-tree regression guard.
LARGE_TREE_SHAPE = (200, 100)
# Time budget of a preview over the large tree (generous: a regression guard, not a benchmark).
LARGE_TREE_BUDGET_S = 30.0
# Media files below `MOVIES`.
MOVIES_FILES = 4
# Files of the nested scenario.
NESTED_FILES = 2
WITHOUT_HUNGARIAN = (INTERVIEW, PODCAST, FILM_SEQUEL, FESTIVE, EPISODE_ONE, EXTRAS)

# A folder selected with an active filter, a nested folder excluded, a file added by hand.
NESTED_OPERATIONS = (
    include(SERIES, subtitles=subtitles(MISSING_ONE, HUNGARIAN)),
    exclude(f"{SERIES}/S01/Extras"),
    include(f"{MOVIES}/{FILM}"),
)

# Name, operations, expected file names.
type Scenario = tuple[str, list[JsonObject], tuple[str, ...]]
SCENARIOS: tuple[Scenario, ...] = (
    ("everything", [include(MEDIA_ROOT)], EVERY_FILE),
    ("video", [include(MEDIA_ROOT, media_kind="video")], EVERY_FILE[2:]),
    ("audio", [include(MEDIA_ROOT, media_kind="audio")], (INTERVIEW, PODCAST)),
    (
        "subtitles_all",
        [include(MEDIA_ROOT, subtitles=subtitles("all", "en", HUNGARIAN))],
        (FILM, FILM_TWIN, EPISODE_TWO),
    ),
    (
        "subtitles_any",
        [include(MEDIA_ROOT, subtitles=subtitles("any", HUNGARIAN, "de"))],
        (FILM, FILM_TWIN, FESTIVE, EPISODE_TWO),
    ),
    # Forced subtitles do not count; Film has a forced and a full Hungarian one, so it is skipped.
    ("missing_hu", [include(MEDIA_ROOT, subtitles=subtitles(MISSING_ONE, HUNGARIAN))], WITHOUT_HUNGARIAN),
    ("missing_en_or_hu", [include(MEDIA_ROOT, subtitles=subtitles(MISSING_ONE, "en", HUNGARIAN))], WITHOUT_HUNGARIAN),
    (
        "name_starts_with",
        [include(MEDIA_ROOT, name=name_filter("starts_with", "SHOW s01"))],
        (EPISODE_ONE, EPISODE_TWO),
    ),
    ("name_ends_with", [include(MEDIA_ROOT, name=name_filter("ends_with", "e02"))], (EPISODE_TWO,)),
    ("name_contains", [include(MEDIA_ROOT, name=name_filter("contains", "ŰSOR"))], (FESTIVE,)),
    (
        "name_regex",
        [include(MEDIA_ROOT, name=name_filter("regex", r"^film(\.part\d)?$"))],
        (FILM, FILM_TWIN, FILM_SEQUEL),
    ),
    (
        "combined_filters",
        [include(MEDIA_ROOT, media_kind="video", name=name_filter("contains", "s01"))],
        (EPISODE_ONE, EPISODE_TWO),
    ),
    (
        "nested_with_filter",
        list(NESTED_OPERATIONS),
        (FILM, EPISODE_ONE),
    ),
    # Later operations override earlier ones, also for a single file.
    (
        "exclude_then_include",
        [include(MEDIA_ROOT), exclude(AUDIO, media_kind="audio"), include(f"{AUDIO}/{INTERVIEW}")],
        tuple(name for name in EVERY_FILE if name != PODCAST),
    ),
    ("nothing", [], ()),
)


@pytest.mark.parametrize(("scenario", "operations", "expected"), SCENARIOS)
def test_preview_selection(
    tmp_path: Path,
    scenario: str,
    operations: list[JsonObject],
    expected: tuple[str, ...],
) -> None:
    """Each filter and operation order selects the expected files, the same on both legs."""
    exchange = run_one(tmp_path, preview_request(operations), prepare=build_media_tree)
    assert exchange.exit_code == EXIT_OK
    assert file_names(exchange) == list(expected)
    assert_golden(f"preview_selection_{scenario}.json", exchange.golden())


def test_folder_states_and_file_details(tmp_path: Path) -> None:
    """A folder is `all` when every media file below it is selected, `partial` when only some are."""
    exchange = run_one(tmp_path, preview_request(NESTED_OPERATIONS), prepare=build_media_tree)
    response = response_of(exchange)
    states = {Path(folder["path"]).name: folder["state"] for folder in response["folders"]}
    assert states == {"media": "partial", "Movies": "partial", "Series": "partial", "S01": "partial"}
    film = response["files"][0]
    assert film["subtitle_languages"] == ["en", HUNGARIAN]
    assert film["forced_subtitle_languages"] == [HUNGARIAN]
    assert response["counts"] == {"files": NESTED_FILES, "video_files": NESTED_FILES, "audio_files": 0, "size_bytes": 0}


def test_whole_folder_is_all(tmp_path: Path) -> None:
    """Every media file of a folder selected: the folder is `all`, its parent `partial`."""
    exchange = run_one(tmp_path, preview_request([include(AUDIO)]), prepare=build_media_tree)
    states = [folder["state"] for folder in response_of(exchange)["folders"]]
    assert states == ["partial", "all"]


def test_paths_without_media_give_warnings(tmp_path: Path) -> None:
    """A path that does not exist or holds no media file is reported, not an error."""
    operations = [include(f"{MEDIA_ROOT}/Nope"), include(f"{MEDIA_ROOT}/Empty"), include(MOVIES)]
    exchange = run_one(tmp_path, preview_request(operations), prepare=build_media_tree)
    assert exchange.exit_code == EXIT_OK
    assert warning_codes(exchange) == [NO_MEDIA_AT_PATH, NO_MEDIA_AT_PATH]
    assert len(file_names(exchange)) == MOVIES_FILES


@pytest.mark.parametrize(
    ("name", "path"),
    [
        ("sibling", "<root>/elsewhere"),
        ("parent", "<root>"),
        ("dot_dot", f"{MEDIA_ROOT}/../media/Movies"),
        ("relative", "media/Movies"),
    ],
)
def test_paths_outside_the_root_are_refused(tmp_path: Path, name: str, path: str) -> None:
    """An operation path outside the scanned folder (or with `..`) is an error."""
    exchange = run_one(tmp_path, preview_request([include(path)]), prepare=build_media_tree)
    assert exchange.exit_code == EXIT_ERROR
    assert error_code(exchange) == "path_outside_root"
    assert_golden(f"preview_selection_outside_{name}.json", exchange.golden())


@pytest.mark.parametrize(
    ("name", "filters"),
    [
        ("regex", {"name": name_filter("regex", "(unclosed")}),
        ("language", {"subtitles": subtitles("any", "xx")}),
        ("no_languages", {"subtitles": subtitles("any")}),
        ("empty_text", {"name": name_filter("contains", "")}),
    ],
)
def test_invalid_filters_are_refused(tmp_path: Path, name: str, filters: JsonObject) -> None:
    """Filters are validated against machine-independent rules only."""
    exchange = run_one(tmp_path, preview_request([include(MEDIA_ROOT, **filters)]), prepare=build_media_tree)
    assert exchange.exit_code == EXIT_INVALID_REQUEST
    assert_golden(f"preview_selection_invalid_{name}.json", exchange.golden())


def _preview(root: Path, rules: PathRules, path: str) -> PreviewSelectionResponse | ErrorResponse:
    ports = dataclasses.replace(seeded_ports(), path_rules=rules)
    document = {"action": "preview_selection", "scan": {"root": str(root)}, "operations": [include(path)]}
    response = Backend(ports).execute(PreviewSelectionRequest.model_validate(document))
    assert isinstance(response, PreviewSelectionResponse | ErrorResponse), response
    return response


SELECTION_TREE = ("media/Movies/Café.mkv", "media/Movies/other.mkv", "media/Audio/song.mp3")
CAFE = "Café.mkv"


@pytest.mark.parametrize(
    ("rules", "relative", "expected"),
    [
        (windows_rules(), "MOVIES/cafÉ.MKV", [CAFE]),
        (windows_rules(), r"movies\CAFÉ.mkv", [CAFE]),
        (macos_rules(), "movies/Café.mkv", [CAFE]),
        (posix_rules(), "Movies/Café.mkv", [CAFE]),
        (posix_rules(), "movies/Café.mkv", []),
        (windows_rules(), "Movies/Café.mkv", []),
    ],
)
def test_selection_paths_compare_under_the_path_rule(
    tmp_path: Path,
    rules: PathRules,
    relative: str,
    expected: list[str],
) -> None:
    """Case folds on Windows and macOS, Unicode normalisation only on macOS, backslash only on Windows."""
    build_tree(tmp_path, SELECTION_TREE)
    root = media_dir(tmp_path)
    response = _preview(root, rules, f"{root}/{relative}")
    assert isinstance(response, PreviewSelectionResponse)
    assert [found.name for found in response.files] == expected
    warnings = [] if expected else [NO_MEDIA_AT_PATH]
    assert [warning.code for warning in response.warnings] == warnings


def test_root_with_another_case_matches_on_windows(tmp_path: Path) -> None:
    """The root part of an operation path compares under the rule too (drive letters, folder case)."""
    build_tree(tmp_path, SELECTION_TREE)
    root = media_dir(tmp_path)
    upper_root = str(root.parent / "MEDIA")
    response = _preview(root, windows_rules(), f"{upper_root}/Audio")
    assert isinstance(response, PreviewSelectionResponse)
    assert [found.name for found in response.files] == ["song.mp3"]
    refused = _preview(root, posix_rules(), f"{upper_root}/Audio")
    assert isinstance(refused, ErrorResponse)
    assert refused.error.code == "path_outside_root"


def test_large_tree_previews_within_budget(tmp_path: Path) -> None:
    """A filtered preview over 20 000 files stays within a generous budget (regression guard)."""
    folders, per_folder = LARGE_TREE_SHAPE
    names = (
        f"media/folder{folder:03}/clip{number:03}.mkv" for folder in range(folders) for number in range(per_folder)
    )
    build_tree(tmp_path, names)
    root = media_dir(tmp_path)
    operations = [include(str(root), name=name_filter("ends_with", "7")), exclude(str(root / "folder000"))]
    document = {"action": "preview_selection", "scan": {"root": str(root)}, "operations": operations}
    started = time.monotonic()
    response = Backend(seeded_ports()).execute(PreviewSelectionRequest.model_validate(document))
    elapsed = time.monotonic() - started
    assert isinstance(response, PreviewSelectionResponse)
    assert response.counts.files == (folders - 1) * per_folder // 10
    assert elapsed < LARGE_TREE_BUDGET_S, f"preview took {elapsed:.1f} s"
