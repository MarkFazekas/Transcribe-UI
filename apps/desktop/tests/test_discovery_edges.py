"""Edge cases of the scan: junctions, unreadable folders, depth, AppleDouble files, sidecar stems."""

import importlib
import os
import sys
from pathlib import Path

import pytest

from support.discovery import media_dir, scan_direct
from support.fakes import seeded_ports
from support.trees import build_tree
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import ErrorResponse
from transcribe_ui.discovery import scan as scan_module
from transcribe_ui.discovery.classes import ScanTreeRequest
from transcribe_ui.discovery.constants import MAX_SCAN_DEPTH

UNREADABLE = "unreadable"
TOO_DEEP = "too_deep"
# Mode of a folder nobody may list.
LOCKED_MODE = 0
# Mode restored before the temp tree is removed.
OPEN_MODE = 0o755
# Folder levels below `MAX_SCAN_DEPTH` the deep tree goes.
DEEP_EXTRA_LEVELS = 20
# Folder levels of the deep tree.
DEEP_LEVELS = MAX_SCAN_DEPTH + DEEP_EXTRA_LEVELS


def _unprivileged_posix() -> bool:
    if sys.platform == "win32":
        return False
    return os.geteuid() != 0


def _build(root: Path, names: list[str]) -> Path:
    build_tree(root, names)
    return media_dir(root)


needs_permissions = pytest.mark.skipif(
    not _unprivileged_posix(),
    reason="needs POSIX permissions that apply (not Windows, not root)",
)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions")
def test_scan_skips_windows_junctions(tmp_path: Path) -> None:
    """A junction pointing back up the tree is reported and not followed."""
    media = _build(tmp_path, ["media/a/clip.mkv"])
    windows_api = importlib.import_module("_winapi")
    windows_api.CreateJunction(str(media), str(media / "a" / "loop"))
    response = scan_direct(media)
    assert [found.name for found in response.files] == ["clip.mkv"]
    assert [warning.code for warning in response.warnings] == ["link_skipped"]


@needs_permissions
def test_unreadable_folder_is_reported(tmp_path: Path) -> None:
    """A subfolder that cannot be listed gives a warning; the rest is scanned."""
    media = _build(tmp_path, ["media/ok.mkv", "media/locked/clip.mkv"])
    locked = media / "locked"
    locked.chmod(LOCKED_MODE)
    try:
        response = scan_direct(media)
    finally:
        locked.chmod(OPEN_MODE)
    assert [found.name for found in response.files] == ["ok.mkv"]
    assert [(warning.code, Path(warning.path).name) for warning in response.warnings] == [(UNREADABLE, "locked")]


@needs_permissions
def test_unreadable_root_is_an_error(tmp_path: Path) -> None:
    """A root that cannot be listed is refused with `root_unreadable`."""
    media = _build(tmp_path, ["media/ok.mkv"])
    media.chmod(LOCKED_MODE)
    try:
        response = Backend(seeded_ports()).execute(ScanTreeRequest(action="scan_tree", root=str(media)))
    finally:
        media.chmod(OPEN_MODE)
    assert isinstance(response, ErrorResponse)
    assert response.error.code == "root_unreadable"


def test_too_deep_folders_are_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Folders below the depth limit are not scanned and give a warning."""
    monkeypatch.setattr(scan_module, "MAX_SCAN_DEPTH", 2)
    media = _build(tmp_path, ["media/a/b/shallow.mkv", "media/a/b/c/deep.mkv"])
    response = scan_direct(media)
    assert [found.name for found in response.files] == ["shallow.mkv"]
    assert [(warning.code, Path(warning.path).name) for warning in response.warnings] == [(TOO_DEEP, "c")]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows limits path length")
def test_deep_tree_does_not_exhaust_the_stack(tmp_path: Path) -> None:
    """A tree deeper than the limit is walked without recursion errors and cut at the limit."""
    levels = "/".join("d" for _level in range(DEEP_LEVELS))
    media = _build(tmp_path, ["media/top.mkv", f"media/{levels}/bottom.mkv"])
    response = scan_direct(media)
    assert [found.name for found in response.files] == ["top.mkv"]
    assert [warning.code for warning in response.warnings] == [TOO_DEEP]


def test_apple_double_files_are_never_media(tmp_path: Path) -> None:
    """`._` metadata files are left out even when hidden entries are scanned."""
    media = _build(tmp_path, ["media/Film.mkv", "media/._Film.mkv", "media/._Film.en.srt"])
    response = scan_direct(media, include_hidden=True)
    assert [found.name for found in response.files] == ["Film.mkv"]
    assert response.files[0].subtitles == ()


def test_shorter_stem_needs_a_known_token(tmp_path: Path) -> None:
    """`Show.S01E02.en.srt` is not `Show.mkv`'s; a language, `forced` or a tag after the stem is."""
    names = ("Show.mkv", "Show.S01E02.en.srt", "Show.cd1.srt", "Show.sdh.en.srt", "Show.forced.srt")
    media = _build(tmp_path, [f"media/{name}" for name in names])
    response = scan_direct(media)
    sidecars = [(sidecar.name, sidecar.language, sidecar.forced) for sidecar in response.files[0].subtitles]
    assert sidecars == [
        ("Show.cd1.srt", None, False),
        ("Show.forced.srt", None, True),
        ("Show.sdh.en.srt", "en", False),
    ]
