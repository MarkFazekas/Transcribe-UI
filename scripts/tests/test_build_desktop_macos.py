"""The macOS floor of a desktop build: wheel tags, Mach-O minimum versions, the bundle's sources."""

from collections.abc import Mapping
from importlib.metadata import PathDistribution
from pathlib import Path
from types import MappingProxyType

from build_desktop_macos import Bundled, bundled_binaries, floor_report, minimum_versions
from build_desktop_wheels import MacosVersion, Wheel, installed_wheels, wheel_tags

# The binding library of Qt: its wheel is tagged for macOS 14, its own dylib says 26.0.
BINDING = Wheel("shiboken6", "6.12.0", ("cp310-abi3-macosx_14_0_universal2",))
# A wheel built for a newer macOS than the floor.
TOO_NEW = Wheel("newer", "1.0", ("cp313-cp313-macosx_15_0_arm64",))
# The bundled binaries of the floor tests: two from wheels, two collected from elsewhere.
BINARIES = (
    Bundled("shiboken6/libshiboken6.abi3.6.12.dylib", Path("/site/shiboken6/libshiboken6.abi3.6.12.dylib")),
    Bundled("newer/_ext.so", Path("/site/newer/_ext.so")),
    Bundled("libpython3.13.dylib", Path("/py/lib/libpython3.13.dylib")),
    Bundled("libav.dylib", Path("/ffmpeg/libav.dylib")),
)
# The minimum macOS `otool` would report for each source.
MINIMUMS: Mapping[str, list[MacosVersion]] = MappingProxyType(
    {
        "/site/shiboken6/libshiboken6.abi3.6.12.dylib": [(26, 0), (26, 0)],
        "/site/newer/_ext.so": [(15, 0)],
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


def test_installed_files_map_to_their_wheel(tmp_path: Path) -> None:
    """Every file of a RECORD is found by its resolved path and by its path inside site-packages."""
    dist_info = tmp_path / "shiboken6-6.12.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text("Metadata-Version: 2.1\nName: shiboken6\nVersion: 6.12.0\n", encoding="utf-8")
    (dist_info / "WHEEL").write_text("Tag: cp310-abi3-macosx_14_0_universal2\n", encoding="utf-8")
    (dist_info / "RECORD").write_text("shiboken6/Shiboken.abi3.so,,\n", encoding="utf-8")
    # `files` lists only the files that exist.
    (tmp_path / "shiboken6").mkdir()
    (tmp_path / "shiboken6" / "Shiboken.abi3.so").write_bytes(b"")
    owners = installed_wheels([PathDistribution(dist_info)])
    assert owners["shiboken6/Shiboken.abi3.so"] == BINDING
    assert owners[str((tmp_path / "shiboken6" / "Shiboken.abi3.so").resolve())] == BINDING


def test_floor_follows_the_tag_else_the_minos() -> None:
    """A wheel's dylib above the floor passes when its tag is at the floor; a too-new tag or binary fails."""
    owners = {"shiboken6/libshiboken6.abi3.6.12.dylib": BINDING, "/site/newer/_ext.so": TOO_NEW}
    report = floor_report(BINARIES, owners, _read)
    assert report.problems == (
        "libav.dylib needs macOS 15.2 (the floor is 14.0)",
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
