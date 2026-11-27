"""Flow tests of `scan_tree` (architecture §3.2; plan m5 §3 M5.3).

Each request runs through the backend and the CLI (parity); golden files are regenerated with
`UPDATE_SNAPSHOTS=1 uv run pytest apps/desktop/tests`.
"""

import ctypes
import dataclasses
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from support.discovery import (
    EVERY_FILE,
    FILM_SEQUEL,
    FILM_TWIN,
    MEDIA_ROOT,
    build_media_tree,
    error_code,
    file_names,
    media_dir,
    response_of,
    scan_request,
    warning_codes,
)
from support.fakes import seeded_ports
from support.golden import assert_golden
from support.parity import RecordingProgress, run_one
from support.trees import build_tree
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import ErrorResponse
from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.cli.constants import EXIT_ERROR, EXIT_INVALID_REQUEST, EXIT_OK
from transcribe_ui.discovery.classes import ScanTreeRequest, ScanTreeResponse
from transcribe_ui.discovery.constants import SCAN_PROGRESS_INTERVAL
from transcribe_ui.discovery.rules import PathRules, macos_rules, posix_rules, windows_rules

# Files of the large-tree regression guard: folders x files per folder.
LARGE_TREE_SHAPE = (200, 100)
# Time budget of scanning the large tree (generous: a regression guard, not a benchmark).
LARGE_TREE_BUDGET_S = 30.0
# Entries of `MEDIA_TREE` only a scan with hidden entries finds.
HIDDEN_MEDIA = (".cover.mp4", "secret.mkv")
# Files of the Windows attribute test, and how many of them a scan with hidden entries finds.
ATTRIBUTE_FILES = 3
LINK_SKIPPED = "link_skipped"


def _scan(root: Path, rules: PathRules | None = None, **fields: object) -> ScanTreeResponse:
    ports = seeded_ports()
    if rules is not None:
        ports = dataclasses.replace(ports, path_rules=rules)
    request = ScanTreeRequest.model_validate({"action": "scan_tree", "root": str(root), **fields})
    response = Backend(ports).execute(request)
    assert isinstance(response, ScanTreeResponse), response
    return response


def _subtitles(response: ScanTreeResponse) -> dict[str, list[str]]:
    return {found.name: [sidecar.name for sidecar in found.subtitles] for found in response.files}


def test_scan_finds_media_and_sidecars(tmp_path: Path) -> None:
    """Media files with their sidecars; hidden entries, orphans and folders without media left out."""
    exchange = run_one(tmp_path, scan_request(), prepare=build_media_tree)
    assert exchange.exit_code == EXIT_OK
    assert file_names(exchange) == list(EVERY_FILE)
    files = {found["name"]: found for found in response_of(exchange)["files"]}
    film = [(sidecar["language"], sidecar["forced"]) for sidecar in files[FILM_TWIN]["subtitles"]]
    assert film == [("en", False), ("hu", True), ("hu", False)]
    second_part = [(sidecar["extension"], sidecar["language"]) for sidecar in files[FILM_SEQUEL]["subtitles"]]
    assert second_part == [("vtt", "pt"), ("srt", None)]
    counts = {"folders": 6, "video_files": 7, "audio_files": 2, "subtitle_files": 10}
    assert response_of(exchange)["counts"] == counts
    assert_golden("scan_tree.json", exchange.golden())


def test_scan_includes_hidden_entries_when_asked(tmp_path: Path) -> None:
    """With `include_hidden`, hidden folders, files and sidecars are scanned too."""
    exchange = run_one(tmp_path, scan_request(include_hidden=True), prepare=build_media_tree)
    names = file_names(exchange)
    assert set(names) == {*EVERY_FILE, *HIDDEN_MEDIA}
    films = {found["name"]: found for found in response_of(exchange)["files"]}
    # A dot-prefixed subtitle is not `<stem>.…` (macOS `._` AppleDouble files): never a sidecar.
    assert ".Film.de.srt" not in [sidecar["name"] for sidecar in films[FILM_TWIN]["subtitles"]]


def test_scan_uses_the_requested_extensions(tmp_path: Path) -> None:
    """Extension classes come from the request; `.MP3` matches `mp3` whatever its case."""
    exchange = run_one(
        tmp_path,
        scan_request(video_extensions=["webm"], audio_extensions=["mp3"]),
        prepare=build_media_tree,
    )
    assert file_names(exchange) == ["podcast.MP3", "Show behind the scenes.webm"]


@pytest.mark.parametrize(
    ("name", "root", "code"),
    [
        ("missing", "<root>/missing", "root_not_found"),
        ("file", f"{MEDIA_ROOT}/Movies/notes.txt", "root_not_a_folder"),
        ("relative", "media", "root_not_absolute"),
    ],
)
def test_scan_refuses_a_bad_root(tmp_path: Path, name: str, root: str, code: str) -> None:
    """A root that is not an absolute path of a folder is refused with its own code."""
    exchange = run_one(tmp_path, scan_request(root), prepare=build_media_tree)
    assert exchange.exit_code == EXIT_ERROR
    assert error_code(exchange) == code
    assert_golden(f"scan_tree_root_{name}.json", exchange.golden())


@pytest.mark.parametrize(
    ("name", "fields"),
    [
        ("overlap", {"video_extensions": ["mkv"], "audio_extensions": ["mkv"]}),
        ("sidecar", {"video_extensions": ["srt"]}),
        ("dotted", {"audio_extensions": [".mp3"]}),
    ],
)
def test_scan_refuses_bad_extensions(tmp_path: Path, name: str, fields: dict[str, object]) -> None:
    """Overlapping, subtitle or malformed extensions make the request invalid."""
    exchange = run_one(tmp_path, scan_request(MEDIA_ROOT, **fields), prepare=build_media_tree)
    assert exchange.exit_code == EXIT_INVALID_REQUEST
    assert_golden(f"scan_tree_extensions_{name}.json", exchange.golden())


def _link_tree(root: Path) -> None:
    build_tree(root, ["media/a/clip.mkv"])
    media = media_dir(root)
    (media / "a" / "loop").symlink_to(media, target_is_directory=True)
    (media / "link.mkv").symlink_to(media / "a" / "clip.mkv")


def test_scan_does_not_follow_links(tmp_path: Path) -> None:
    """A symlink loop and a file symlink are reported and skipped; the scan ends."""
    probe = tmp_path / "probe"
    try:
        probe.symlink_to(tmp_path)
    except OSError:
        pytest.skip("creating symlinks needs a privilege on this machine")
    exchange = run_one(tmp_path, scan_request(), prepare=_link_tree)
    assert file_names(exchange) == ["clip.mkv"]
    assert warning_codes(exchange) == [LINK_SKIPPED, LINK_SKIPPED]


def _invalid_name_tree(root: Path) -> None:
    build_tree(root, ["media/good.mkv"])
    bad = b"/".join((os.fsencode(media_dir(root)), b"bad\xff.mkv"))
    Path(os.fsdecode(bad)).touch()


@pytest.mark.skipif(sys.platform != "linux", reason="only Linux file systems accept names that are not UTF-8")
def test_scan_reports_names_that_are_not_unicode(tmp_path: Path) -> None:
    """A name that is not valid UTF-8 is reported (U+FFFD in its place) and left out."""
    exchange = run_one(tmp_path, scan_request(), prepare=_invalid_name_tree)
    assert file_names(exchange) == ["good.mkv"]
    warning = response_of(exchange)["warnings"][0]
    assert warning["code"] == "invalid_name"
    assert warning["path"].endswith("/media/bad\ufffd.mkv")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file attributes")
def test_scan_skips_windows_hidden_and_system_entries(tmp_path: Path) -> None:
    """Entries with the Windows hidden or system attribute are skipped unless asked."""
    build_tree(tmp_path, ["media/shown.mkv", "media/hidden.mkv", "media/system.mkv"])
    media = media_dir(tmp_path)
    if sys.platform == "win32":  # Lets the type checker see `ctypes.windll`.
        set_attributes = ctypes.windll.kernel32.SetFileAttributesW
        assert set_attributes(str(media / "hidden.mkv"), stat.FILE_ATTRIBUTE_HIDDEN)
        assert set_attributes(str(media / "system.mkv"), stat.FILE_ATTRIBUTE_SYSTEM)
    assert [found.name for found in _scan(media).files] == ["shown.mkv"]
    assert len(_scan(media, include_hidden=True).files) == ATTRIBUTE_FILES


SIDECAR_CASE_TREE = ("media/Movie.MKV", "media/movie.EN.srt", "media/MOVIE.hu.forced.SRT")
# A decomposed media name (as older macOS volumes store names) with a composed sidecar name.
SIDECAR_DECOMPOSED_TREE = ("media/Café.mkv", "media/Café.en.srt")


@pytest.mark.parametrize(
    ("rules", "tree", "expected"),
    [
        (windows_rules(), SIDECAR_CASE_TREE, ["movie.EN.srt", "MOVIE.hu.forced.SRT"]),
        (macos_rules(), SIDECAR_CASE_TREE, ["movie.EN.srt", "MOVIE.hu.forced.SRT"]),
        (posix_rules(), SIDECAR_CASE_TREE, []),
        (macos_rules(), SIDECAR_DECOMPOSED_TREE, ["Café.en.srt"]),
        (windows_rules(), SIDECAR_DECOMPOSED_TREE, []),
        (posix_rules(), SIDECAR_DECOMPOSED_TREE, []),
    ],
)
def test_sidecar_stems_compare_under_the_path_rule(
    tmp_path: Path,
    rules: PathRules,
    tree: tuple[str, ...],
    expected: list[str],
) -> None:
    """Case folds on Windows and macOS, Unicode normalisation only on macOS."""
    build_tree(tmp_path, tree)
    response = _scan(media_dir(tmp_path), rules)
    assert len(response.files) == 1
    assert next(iter(_subtitles(response).values())) == expected
    assert response.path_flavor == rules.flavor


def test_scan_stops_when_cancelled(tmp_path: Path) -> None:
    """A set cancel token stops the scan with `cancelled`."""
    build_media_tree(tmp_path)
    cancel = CancelFlag()
    cancel.cancel()
    request = ScanTreeRequest(action="scan_tree", root=str(media_dir(tmp_path)))
    response = Backend(seeded_ports()).execute(request, cancel=cancel)
    assert isinstance(response, ErrorResponse)
    assert response.error.code == "cancelled"


def test_large_tree_scans_within_budget(tmp_path: Path) -> None:
    """20 000 files scan within a generous budget and report progress (regression guard)."""
    folders, per_folder = LARGE_TREE_SHAPE
    names = (
        f"media/folder{folder:03}/clip{number:03}.mkv" for folder in range(folders) for number in range(per_folder)
    )
    build_tree(tmp_path, names)
    progress = RecordingProgress()
    request = ScanTreeRequest(action="scan_tree", root=str(media_dir(tmp_path)))
    started = time.monotonic()
    response = Backend(seeded_ports()).execute(request, progress)
    elapsed = time.monotonic() - started
    assert isinstance(response, ScanTreeResponse)
    assert len(response.files) == folders * per_folder
    assert len(progress.events) == folders * per_folder // SCAN_PROGRESS_INTERVAL
    assert elapsed < LARGE_TREE_BUDGET_S, f"scan took {elapsed:.1f} s"
