"""The app version: a release build's stamp, else the installed distribution's (plan m8 §1 "Version stamping")."""

from pathlib import Path

import pytest

from transcribe_ui.api import info


def test_app_version_without_a_stamp() -> None:
    """From source there is no stamp: the version is the installed distribution's (the goldens' 0.1.0)."""
    assert info.app_version() == "0.1.0"


def test_stamped_version_reads_the_stamp(tmp_path: Path) -> None:
    """A release build's stamp is read without surrounding whitespace; a missing or empty stamp is None."""
    stamp = tmp_path / "version.txt"
    assert info.stamped_version(stamp) is None
    stamp.write_text("", encoding="utf-8")
    assert info.stamped_version(stamp) is None
    stamp.write_text("0.1.0+20261128.abc1234\n", encoding="utf-8")
    assert info.stamped_version(stamp) == "0.1.0+20261128.abc1234"


def test_app_version_prefers_the_stamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the stamp as package data (a release build), `--version` and `get_app_info` show it."""
    (tmp_path / "version.txt").write_text("0.1.0+20261128.abc1234\n", encoding="utf-8")
    monkeypatch.setattr(info, "files", lambda _package: tmp_path)
    assert info.app_version() == "0.1.0+20261128.abc1234"
