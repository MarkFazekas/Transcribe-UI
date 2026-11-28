"""The macOS floor of a desktop build: wheel tags, Mach-O minimum versions, the bundle's sources."""

from collections.abc import Callable, Mapping
from importlib.metadata import PathDistribution
from pathlib import Path
from types import MappingProxyType

import pytest
from build_desktop_macos import (
    Bundled,
    bundled_binaries,
    check_bundle,
    executable_problems,
    floor_report,
    minimum_versions,
)
from build_desktop_wheels import MacosVersion, Wheel, installed_wheels, wheel_tags

# The binding library of Qt: its wheel is tagged for macOS 14, its own dylib says 26.0.
BINDING = Wheel("shiboken6", "6.12.0", ("cp310-abi3-macosx_14_0_universal2",))
# A wheel built for a newer macOS than the floor.
TOO_NEW = Wheel("newer", "1.0", ("cp313-cp313-macosx_15_0_arm64",))
# A wheel whose tags name no macOS (its binaries follow the minos rule).
UNTAGGED = Wheel("untagged", "1.0", ("py3-none-any",))
# The bundled binaries of the floor tests: three from wheels, three collected from elsewhere.
BINARIES = (
    Bundled("shiboken6/libshiboken6.abi3.6.12.dylib", Path("/site/shiboken6/libshiboken6.abi3.6.12.dylib")),
    Bundled("newer/_ext.so", Path("/site/newer/_ext.so")),
    Bundled("untagged/_ext.so", Path("/site/untagged/_ext.so")),
    Bundled("libpython3.13.dylib", Path("/py/lib/libpython3.13.dylib")),
    Bundled("libav.dylib", Path("/ffmpeg/libav.dylib")),
    # Named like the wheel's file, but collected from elsewhere (a package manager's copy): no wheel's tag covers it.
    Bundled("shiboken6/Shiboken.abi3.so", Path("/opt/homebrew/lib/Shiboken.abi3.so")),
)
# The wheels of the floor tests, by the resolved source of their files.
OWNERS = MappingProxyType(
    {
        "/site/shiboken6/libshiboken6.abi3.6.12.dylib": BINDING,
        "/site/shiboken6/Shiboken.abi3.so": BINDING,
        "/site/newer/_ext.so": TOO_NEW,
        "/site/untagged/_ext.so": UNTAGGED,
    },
)
# The minimum macOS `otool` would report for each source.
MINIMUMS: Mapping[str, list[MacosVersion]] = MappingProxyType(
    {
        "/site/shiboken6/libshiboken6.abi3.6.12.dylib": [(26, 0), (26, 0)],
        "/site/newer/_ext.so": [(15, 0)],
        "/site/untagged/_ext.so": [(14, 4)],
        "/opt/homebrew/lib/Shiboken.abi3.so": [(15, 0)],
        "/out/dist/transcribe-ui-macos-arm64-gui": [(11, 0)],
        "/out/dist/too-new": [(14, 0), (15, 0)],
        "/out/dist/unversioned": [],
        "/py/lib/libpython3.13.dylib": [(14, 0)],
        "/ffmpeg/libav.dylib": [(14, 0), (15, 2)],
    }
)


def _read(binary: Path) -> list[MacosVersion]:
    return MINIMUMS[binary.as_posix()]


def test_wheel_tags_and_their_macos_floor() -> None:
    """Tags come from the `Tag:` lines; the floor is the highest macOS of the `macosx` tags."""
    text = "Wheel-Version: 1.0\nTag: cp313-cp313-macosx_10_13_x86_64\nTag: cp313-cp313-macosx_11_0_arm64\n"
    wheel = Wheel("x", "1", wheel_tags(text))
    assert wheel.tags == ("cp313-cp313-macosx_10_13_x86_64", "cp313-cp313-macosx_11_0_arm64")
    assert wheel.macos_floor() == (11, 0)
    assert Wheel("pure", "1", ("py3-none-any",)).macos_floor() is None


def test_a_compressed_tag_counts_its_highest_macos() -> None:
    """`macosx_10_9_x86_64.macosx_15_0_arm64` is one tag naming two platforms: 15.0 counts."""
    assert Wheel("x", "1", ("cp313-cp313-macosx_10_9_x86_64.macosx_15_0_arm64",)).macos_floor() == (15, 0)


def test_installed_files_map_to_their_wheel(tmp_path: Path) -> None:
    """Every file of a RECORD is found by its resolved path only (not by its path inside site-packages)."""
    dist_info = tmp_path / "shiboken6-6.12.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text("Metadata-Version: 2.1\nName: shiboken6\nVersion: 6.12.0\n", encoding="utf-8")
    (dist_info / "WHEEL").write_text("Tag: cp310-abi3-macosx_14_0_universal2\n", encoding="utf-8")
    (dist_info / "RECORD").write_text("shiboken6/Shiboken.abi3.so,,\n", encoding="utf-8")
    # `files` lists only the files that exist.
    (tmp_path / "shiboken6").mkdir()
    (tmp_path / "shiboken6" / "Shiboken.abi3.so").write_bytes(b"")
    owners = installed_wheels([PathDistribution(dist_info)])
    assert owners == {str((tmp_path / "shiboken6" / "Shiboken.abi3.so").resolve()): BINDING}


def test_floor_follows_the_tag_else_the_minos() -> None:
    """A wheel's dylib above the floor passes when its tag is at the floor; a too-new tag or binary fails.

    A binary of a wheel without a `macosx` tag, and one named like a wheel's file but collected
    from elsewhere, follow the minos rule.
    """
    report = floor_report(BINARIES, OWNERS, _read)
    assert report.problems == (
        "untagged/_ext.so needs macOS 14.4 (the floor is 14.0)",
        "libav.dylib needs macOS 15.2 (the floor is 14.0)",
        "shiboken6/Shiboken.abi3.so needs macOS 15.0 (the floor is 14.0)",
        "wheel newer 1.0 is tagged for macOS 15.0",
    )
    assert report.wheels == (
        "wheel newer 1.0: tags cp313-cp313-macosx_15_0_arm64; highest minos 15.0",
        "wheel shiboken6 6.12.0: tags cp310-abi3-macosx_14_0_universal2; highest minos 26.0",
    )


def test_minimum_versions_of_each_architecture() -> None:
    """`otool -arch all -l` of a universal file: the minimum macOS of each slice, nothing else."""
    otool = (
        "/py/lib/_ssl.so (architecture x86_64):\n"
        "Load command 9\n      cmd LC_VERSION_MIN_MACOSX\n  cmdsize 16\n  version 10.13\n      sdk 10.15\n"
        "Load command 10\n      cmd LC_SOURCE_VERSION\n  cmdsize 16\n  version 0.0\n"
        "/py/lib/_ssl.so (architecture arm64):\n"
        "Load command 9\n      cmd LC_BUILD_VERSION\n  cmdsize 32\n platform 1\n    minos 14.0\n"
        "      sdk 15.2\n   ntools 1\n     tool 3\n  version 1053.12\n"
    )
    assert minimum_versions(otool) == [(10, 13), (14, 0)]


def test_only_minos_counts_in_the_build_version() -> None:
    """The linker's `version` in the build version command is never read as a minimum macOS."""
    tools_first = "Load command 9\n      cmd LC_BUILD_VERSION\n     tool 3\n  version 1053.12\n    minos 14.0\n"
    without_minos = "Load command 9\n      cmd LC_BUILD_VERSION\n     tool 3\n  version 1053.12\n"
    assert minimum_versions(tools_first) == [(14, 0)]
    assert not minimum_versions(without_minos)


def test_bundled_binaries_from_the_table_of_contents(tmp_path: Path) -> None:
    """Binaries and extensions are taken from PyInstaller's package table; data and modules are not."""
    table = tmp_path / "PKG-00.toc"
    entries = [
        ("PYZ-00.pyz", "/w/PYZ-00.pyz", "PYZ"),
        ("libpython3.13.dylib", "/py/lib/libpython3.13.dylib", "BINARY"),
        ("_ssl.so", "/py/lib/_ssl.so", "EXTENSION"),
        ("transcribe_ui/version.txt", "/w/version.txt", "DATA"),
    ]
    table.write_text(repr(("/w/x.pkg", {"BINARY": True}, entries, False)), encoding="utf-8")
    assert bundled_binaries(table) == [
        Bundled("_ssl.so", Path("/py/lib/_ssl.so")),
        Bundled("libpython3.13.dylib", Path("/py/lib/libpython3.13.dylib")),
    ]


def test_the_executable_follows_the_minos_rule() -> None:
    """The bootloader is checked by its own minimum macOS, whatever wheel PyInstaller came in."""
    assert not executable_problems(Path("/out/dist/transcribe-ui-macos-arm64-gui"), _read)
    assert executable_problems(Path("/out/dist/too-new"), _read) == ["too-new needs macOS 15.0 (the floor is 14.0)"]
    assert executable_problems(Path("/out/dist/unversioned"), _read) == ["unversioned: no minimum macOS found"]


def test_check_bundle_without_otool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Without `otool` the check reports it as a problem instead of passing."""
    monkeypatch.setattr("shutil.which", lambda _name: None)
    report = check_bundle(tmp_path / "PKG-00.toc", tmp_path / "executable")
    assert report.problems == ("otool not found",)


def test_check_bundle_checks_the_executable_too(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A too-new executable fails even though PyInstaller's wheel is tagged at the floor."""
    bundled = tmp_path / "site" / "PyInstaller" / "_internal.so"
    table = tmp_path / "PKG-00.toc"
    entries = [("PyInstaller/_internal.so", str(bundled), "BINARY")]
    table.write_text(repr(("/w/x.pkg", {"BINARY": True}, entries, False)), encoding="utf-8")
    minimums: dict[Path, list[MacosVersion]] = {bundled.resolve(): [(26, 0)], tmp_path / "too-new": [(15, 0)]}
    wheel = Wheel("pyinstaller", "6.22.3", ("py3-none-macosx_14_0_universal2",))
    monkeypatch.setattr("build_desktop_macos.otool_reader", lambda: _reader(minimums))
    monkeypatch.setattr("build_desktop_macos.installed_wheels", lambda: {str(bundled.resolve()): wheel})
    report = check_bundle(table, tmp_path / "too-new")
    assert report.problems == ("too-new needs macOS 15.0 (the floor is 14.0)",)
    assert report.wheels == ("wheel pyinstaller 6.22.3: tags py3-none-macosx_14_0_universal2; highest minos 26.0",)


def _reader(minimums: Mapping[Path, list[MacosVersion]]) -> Callable[[Path], list[MacosVersion]]:
    return lambda binary: minimums[binary.resolve()]
