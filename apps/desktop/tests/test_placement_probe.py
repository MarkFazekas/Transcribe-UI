"""The OS file-system probe of the placement planner (architecture §3.5; plan m5 §3 M5.7)."""

import os
import sys
from pathlib import Path

import pytest

from transcribe_ui.placement.probe import OsPlacementProbe

# Mode of a folder nobody may search.
LOCKED_MODE = 0
# Mode restored before the temp tree is removed.
OPEN_MODE = 0o700


def test_target_kinds(tmp_path: Path) -> None:
    """A missing path, a file and a folder are told apart; a path below a file is not usable."""
    probe = OsPlacementProbe()
    existing = tmp_path / "a.hu.srt"
    existing.write_text("1\n", encoding="utf-8")
    assert probe.target_kind(str(tmp_path / "missing.srt")) == "missing"
    assert probe.target_kind(str(existing)) == "file"
    assert probe.target_kind(str(tmp_path)) == "other"
    if sys.platform != "win32":
        assert probe.target_kind(str(existing / "below.srt")) == "other"


def test_folder_exists(tmp_path: Path) -> None:
    """Only a folder counts as the original folder."""
    probe = OsPlacementProbe()
    existing = tmp_path / "a.mkv"
    existing.write_bytes(b"")
    assert probe.folder_exists(str(tmp_path))
    assert not probe.folder_exists(str(existing))
    assert not probe.folder_exists(str(tmp_path / "gone"))


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need privileges on Windows")
def test_links_are_followed(tmp_path: Path) -> None:
    """A link to a file counts as a file, a dangling link as something in the way; resolve follows links."""
    probe = OsPlacementProbe()
    real = tmp_path / "real"
    real.mkdir()
    (real / "a.srt").write_text("1\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    (tmp_path / "file-link.srt").symlink_to(real / "a.srt")
    (tmp_path / "dangling.srt").symlink_to(tmp_path / "nowhere")
    assert probe.target_kind(str(tmp_path / "file-link.srt")) == "file"
    assert probe.target_kind(str(tmp_path / "dangling.srt")) == "other"
    resolved = probe.resolve(str(link / "new.srt"))
    assert resolved == str(Path(os.path.realpath(real)) / "new.srt")


def _unprivileged_posix() -> bool:
    if sys.platform == "win32":
        return False
    return os.geteuid() != 0


@pytest.mark.skipif(not _unprivileged_posix(), reason="needs POSIX permissions that apply (not Windows, not root)")
def test_unsearchable_folder_is_unreadable(tmp_path: Path) -> None:
    """A target in a folder that cannot be searched is unreadable, not existing."""
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(LOCKED_MODE)
    try:
        assert OsPlacementProbe().target_kind(str(locked / "a.srt")) == "unreadable"
    finally:
        locked.chmod(OPEN_MODE)
