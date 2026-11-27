"""The OS file-system probe of the placement planner (architecture §3.5; plan m5 §3 M5.7)."""

import os
import sys
from pathlib import Path

import pytest

from transcribe_ui.placement.probe import OsPlacementProbe


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
