"""The pure parts of `scripts/build_ffmpeg.py` and its checks (docs/ai/plans/m8.md, M8.2)."""

from pathlib import Path

import pytest
from build_ffmpeg import cache_key
from build_ffmpeg_checks import (
    build_tree_problems,
    buildconf_arguments,
    buildconf_problems,
    configure_lists,
    dependency_problems,
    expand,
    licence_problems,
    linked_libraries,
)
from build_ffmpeg_pins import (
    FFMPEG,
    PLATFORMS,
    SYSTEM_ZLIB,
    WINDOWS,
    ZLIB,
    Platform,
    build_info,
    configure_arguments,
    executable,
    sources,
)
from build_ffmpeg_steps import required_tools

# The program most checks are run on.
FFMPEG_PROGRAM = "ffmpeg"
# The Linux platform.
LINUX: Platform = "linux-x64"
# The library lists of a tiny `configure`, nested like the real one.
CONFIGURE = """
EXTERNAL_LIBRARY_GPL_LIST="
    libx264
"
EXTERNAL_LIBRARY_LIST="
    $EXTERNAL_LIBRARY_GPL_LIST
    libass
"
EXTERNAL_AUTODETECT_LIBRARY_LIST="
    iconv
    lzma
    zlib
"
HWACCEL_LIBRARY_LIST="
    opencl
"
HWACCEL_AUTODETECT_LIBRARY_LIST="
    vaapi
"
THREADS_LIST="
    pthreads
    w32threads
"
"""
# The `config.mak` of an allowed Linux build of that `configure`.
ALLOWED = """
!CONFIG_LIBX264=yes
!CONFIG_LIBASS=yes
!CONFIG_ICONV=yes
!CONFIG_LZMA=yes
CONFIG_ZLIB=yes
!CONFIG_OPENCL=yes
!CONFIG_VAAPI=yes
HAVE_PTHREADS=yes
!HAVE_W32THREADS=yes
!CONFIG_GPL=yes
!CONFIG_NONFREE=yes
!CONFIG_VERSION3=yes
CONFIG_FFMPEG=yes
CONFIG_FFPROBE=yes
"""
# `ldd` of a Linux build that loads one library besides those of glibc.
LDD = """\tlinux-vdso.so.1 (0x00007ffd)
\tlibm.so.6 => /lib64/libm.so.6 (0x00007f00)
\tlibz.so.1 => /lib64/libz.so.1 (0x00007f01)
\tlibc.so.6 => /lib64/libc.so.6 (0x00007f02)
\t/lib64/ld-linux-x86-64.so.2 (0x00007f03)
"""
# `otool -L` of a macOS build that loads a Homebrew library.
OTOOL = """/work/ffmpeg:
\t/usr/lib/libz.1.dylib (compatibility version 1.0.0, current version 1.2.12)
\t/System/Library/Frameworks/CoreFoundation.framework/Versions/A/CoreFoundation (compatibility version 150.0.0)
\t/opt/homebrew/opt/xz/lib/liblzma.5.dylib (compatibility version 11.0.0, current version 11.2.0)
\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1351.0.0)
"""
# The import part of `objdump -p` of a Windows build that needs the mingw thread DLL.
OBJDUMP = """The Import Tables (interpreted .idata section contents)
\tDLL Name: ADVAPI32.dll
\tvma:  Hint/Ord Member-Name Bound-To
\tDLL Name: KERNEL32.dll
\tDLL Name: api-ms-win-crt-runtime-l1-1-0.dll
\tDLL Name: libwinpthread-1.dll
\tDLL Name: msvcrt.dll
"""
# `-hide_banner -buildconf` of a build (on stderr), ending with the exit line of ffmpeg.
BUILDCONF = """
  configuration:
    --disable-autodetect
    --enable-zlib

Exiting with exit code 0
"""
# `-L` of an LGPL 2.1 or later build.
LGPL = """ffmpeg is free software; you can redistribute it and/or
modify it under the terms of the GNU Lesser General Public
License as published by the Free Software Foundation; either
version 2.1 of the License, or (at your option) any later version.
"""
# `-L` of a GPL build.
GPL = """ffmpeg is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.
"""


def test_configure_line_of_linux() -> None:
    """An LGPL build of the two programs from FFmpeg's own components, zlib and pthreads only."""
    assert configure_arguments(LINUX) == [
        "--disable-autodetect",
        "--disable-iconv",
        "--enable-zlib",
        "--enable-pthreads",
        "--enable-static",
        "--disable-shared",
        "--pkg-config-flags=--static",
        "--disable-programs",
        "--enable-ffmpeg",
        "--enable-ffprobe",
        "--disable-doc",
        "--disable-network",
        "--disable-devices",
        "--enable-indev=lavfi",
    ]


def test_configure_lines_differ_only_in_platform_flags() -> None:
    """Windows: its own threads, the mingw-w64 cross build, linked statically; macOS: the deployment target."""
    common = len(configure_arguments(LINUX))
    windows = configure_arguments(WINDOWS)
    macos = configure_arguments("macos-arm64")
    assert windows[3] == "--enable-w32threads"
    assert windows[common:] == [
        "--target-os=mingw32",
        "--arch=x86_64",
        "--cross-prefix=x86_64-w64-mingw32-",
        "--enable-cross-compile",
        "--pkg-config=pkg-config",
        "--extra-ldflags=-static",
    ]
    assert macos[common:] == ["--extra-cflags=-mmacosx-version-min=14.0", "--extra-ldflags=-mmacosx-version-min=14.0"]
    assert configure_arguments("macos-x64") == macos


@pytest.mark.parametrize("target", PLATFORMS)
def test_no_platform_enables_a_licence_switch(target: Platform) -> None:
    """No configure line makes the build GPL, nonfree or version 3."""
    assert not {"--enable-gpl", "--enable-nonfree", "--enable-version3"} & set(configure_arguments(target))


def test_sources_of_each_build() -> None:
    """On macOS, which links the system's libz, the build is made from FFmpeg alone; the others bundle zlib."""
    assert sources(LINUX) == sources(WINDOWS) == [FFMPEG, ZLIB]
    assert sources("macos-x64") == [FFMPEG]
    assert (FFMPEG.archive, FFMPEG.folder, ZLIB.folder) == ("ffmpeg-9.1.tar.xz", "ffmpeg-9.1", "zlib-1.4")
    for source in (FFMPEG, ZLIB):
        assert all(url.endswith(f"/{source.archive}") for url in source.urls)


def test_build_info_states_sources_and_configure_line() -> None:
    """What the notices and release notes read: the sources with their checksums and keys, the configure line."""
    described = build_info("macos-arm64")
    assert (described["zlib"], described["configure"]) == (SYSTEM_ZLIB, configure_arguments("macos-arm64"))
    assert build_info(WINDOWS)["zlib"] == {
        "name": "zlib",
        "version": "1.4",
        "urls": (
            "https://github.com/madler/zlib/releases/download/v1.4/zlib-1.4.tar.gz",
            "https://zlib.net/fossils/zlib-1.4.tar.gz",
        ),
        "sha256": ZLIB.sha256,
        "signing_key": ZLIB.signing_key,
        "licence": "LICENSE",
    }


def test_tools_of_each_build() -> None:
    """The cross build needs the mingw-w64 tools; only the x86 builds need nasm."""
    assert required_tools(WINDOWS) == [
        "curl",
        "make",
        "nasm",
        "x86_64-w64-mingw32-gcc",
        "x86_64-w64-mingw32-objdump",
        "pkg-config",
    ]
    assert required_tools("macos-arm64") == ["curl", "make", "otool"]
    assert required_tools(LINUX) == ["curl", "make", "nasm", "ldd", "pkg-config"]
    assert [executable("ffprobe", target) for target in (WINDOWS, LINUX)] == ["ffprobe.exe", "ffprobe"]


def test_cache_key_follows_the_build_files(tmp_path: Path) -> None:
    """The key names FFmpeg's version and the platform, and changes with the build scripts."""
    script = tmp_path / "build.py"
    script.write_text("one", encoding="utf-8")
    first = cache_key(WINDOWS, [script])
    script.write_text("two", encoding="utf-8")
    assert first.startswith("ffmpeg-9.1-windows-x64-")
    assert cache_key(WINDOWS, [script]) != first
    assert cache_key(LINUX, [script]).startswith("ffmpeg-9.1-linux-x64-")


def test_configure_lists_expand_nested_lists() -> None:
    """`$NAME` items expand to that list's items; an unreadable item or an unknown list is reported."""
    expansion = expand("EXTERNAL_LIBRARY_LIST", configure_lists(CONFIGURE))
    assert (expansion.names, expansion.problems) == ({"libx264", "libass"}, [])
    assert expand("BROKEN", {"BROKEN": ("$MISSING", "a-b", "ok")}).problems == [
        "configure: cannot read the item 'a-b' of BROKEN",
        "configure: cannot read the item '$MISSING' of BROKEN",
    ]


def test_build_tree_of_an_allowed_build() -> None:
    """Only zlib and the platform's thread library on, no licence switch, both programs: no problem."""
    assert not build_tree_problems(CONFIGURE, ALLOWED, "pthreads")


def test_build_tree_problems_name_each_switch() -> None:
    """GPL, an extra library, zlib off, the wrong thread library and a missing program are all reported."""
    config_mak = (
        ALLOWED.replace("!CONFIG_GPL", "CONFIG_GPL")
        .replace("!CONFIG_LZMA", "CONFIG_LZMA")
        .replace("CONFIG_ZLIB", "!CONFIG_ZLIB")
        .replace("CONFIG_FFPROBE=yes", "")
    )
    assert build_tree_problems(CONFIGURE, config_mak, "w32threads") == [
        "config.mak: CONFIG_LZMA is on",
        "config.mak: CONFIG_ZLIB is off",
        "config.mak: HAVE_PTHREADS is on",
        "config.mak: HAVE_W32THREADS is off",
        "config.mak: CONFIG_GPL is on",
        "config.mak: CONFIG_FFPROBE is off",
    ]


def test_build_tree_needs_readable_lists() -> None:
    """A `configure` whose lists cannot be read fails the check instead of passing it."""
    assert build_tree_problems("", ALLOWED, "pthreads") == [
        "configure: the library lists do not name zlib and the thread library",
    ]


def test_linux_programs_load_only_glibc() -> None:
    """`ldd`: glibc's libraries and the kernel's pass; a shared zlib does not."""
    libraries = linked_libraries("linux", LDD)
    assert libraries == ["linux-vdso.so.1", "libm.so.6", "libz.so.1", "libc.so.6", "ld-linux-x86-64.so.2"]
    assert dependency_problems(FFMPEG_PROGRAM, "linux", libraries) == ["ffmpeg loads libz.so.1"]


def test_macos_programs_load_only_system_libraries() -> None:
    """`otool -L`: `/usr/lib` and `/System` pass; a Homebrew library does not."""
    libraries = linked_libraries("macos", OTOOL)
    assert dependency_problems("ffprobe", "macos", libraries) == [
        "ffprobe loads /opt/homebrew/opt/xz/lib/liblzma.5.dylib",
    ]


def test_windows_programs_import_only_windows_dlls() -> None:
    """`objdump -p`: Windows' DLLs and the UCRT API sets pass; the mingw runtime does not."""
    libraries = linked_libraries("windows", OBJDUMP)
    assert libraries == [
        "ADVAPI32.dll",
        "KERNEL32.dll",
        "api-ms-win-crt-runtime-l1-1-0.dll",
        "libwinpthread-1.dll",
        "msvcrt.dll",
    ]
    assert dependency_problems(FFMPEG_PROGRAM, "windows", libraries) == ["ffmpeg loads libwinpthread-1.dll"]


def test_no_library_listed_is_a_problem() -> None:
    """An empty listing means the tool did not read the program, which must not pass."""
    assert dependency_problems(FFMPEG_PROGRAM, "windows", []) == ["ffmpeg: objdump -p lists no library"]


def test_buildconf_is_compared_as_a_whole() -> None:
    """The arguments after the heading, without ffmpeg's exit line; a missing, extra or moved one is reported."""
    arguments = buildconf_arguments(BUILDCONF)
    assert arguments == ["--disable-autodetect", "--enable-zlib"]
    assert not buildconf_problems(FFMPEG_PROGRAM, arguments, ["--disable-autodetect", "--enable-zlib"])
    assert buildconf_problems(
        FFMPEG_PROGRAM, [*arguments, "--enable-lzma"], ["--disable-autodetect", "--enable-zlib"]
    ) == [
        "ffmpeg -buildconf has --enable-lzma",
    ]
    assert buildconf_problems("ffprobe", list(reversed(arguments)), arguments) == [
        "ffprobe -buildconf has the expected arguments in another order",
    ]
    assert not buildconf_arguments("ffmpeg version 9.1")


def test_buildconf_unquotes_the_macos_flags() -> None:
    """Configure quotes a value holding `=`; read with shell rules it equals the configure line."""
    shown = (
        "configuration:\n    --enable-pthreads\n    --extra-cflags='-mmacosx-version-min=14.0'\n"
        "    --extra-ldflags='-mmacosx-version-min=14.0'\n"
    )
    arguments = buildconf_arguments(shown)
    assert arguments == ["--enable-pthreads", *configure_arguments("macos-arm64")[-2:]]


def test_buildconf_keeps_an_unreadable_line() -> None:
    """A line with an unclosed quote is kept as printed, so the comparison fails on it."""
    assert buildconf_arguments("configuration:\n    --extra-cflags='-O2\n") == ["--extra-cflags='-O2"]


def test_buildconf_splits_after_pkg_config() -> None:
    """The program prints the argument after `--pkg-config=pkg-config` on its line; it is read as two."""
    shown = "configuration:\n    --enable-cross-compile\n    --pkg-config=pkg-config --extra-ldflags=-static\n"
    assert buildconf_arguments(shown) == [
        "--enable-cross-compile",
        "--pkg-config=pkg-config",
        "--extra-ldflags=-static",
    ]


@pytest.mark.parametrize(("output", "problems"), [(LGPL, []), (GPL, ["ffmpeg -L does not report LGPL 2.1 or later"])])
def test_licence_must_be_lgpl(output: str, problems: list[str]) -> None:
    """`-L` passes only with the LGPL 2.1 or later notice."""
    assert licence_problems(FFMPEG_PROGRAM, output) == problems
