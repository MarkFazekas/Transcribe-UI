"""The pinned FFmpeg and zlib sources and the configure line of each platform (docs/ai/plans/m8.md §1 "ffmpeg").

Read by `scripts/build_ffmpeg.py` (the build and its checks) and by whatever states the bundled
build: the third-party notices (M8.3) and the release notes (M8.5) take the sources, their
licence files and the configure lines from here (`build_info`, or `build_ffmpeg.py
configuration`), so they always describe what was built. Standard library only.

Every platform builds the same LGPL selection of FFmpeg's own components: no `--enable-gpl`,
`--enable-nonfree` or `--enable-version3`; `--disable-autodetect`, so no library is picked up
unless enabled here; zlib as the only external library (built statically from its pinned source
on Linux and Windows, the system's `libz` on macOS) and the platform's thread library. The lines
differ only in the thread flag and the platform's build flags. A version bump changes the
version, the URL and the SHA-256 (computed from the release whose PGP signature was checked).
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Literal, get_args

# A platform of the release (`<os>-<arch>` of the asset names).
type Platform = Literal["windows-x64", "macos-arm64", "macos-x64", "linux-x64"]
# A program of the build.
type Program = Literal["ffmpeg", "ffprobe"]

# The release platforms.
PLATFORMS: tuple[Platform, ...] = get_args(Platform.__value__)
# The programs of the build.
PROGRAMS: tuple[Program, ...] = get_args(Program.__value__)
# The Windows platform, cross-compiled on Linux.
WINDOWS: Platform = "windows-x64"
# Prefix of the mingw-w64 cross tools.
MINGW_PREFIX = "x86_64-w64-mingw32-"
# The oldest macOS the builds run on (the PySide6 wheel needs 14 too).
MACOS_MINIMUM = "14.0"
# What `build_info` says of the zlib of a macOS build.
SYSTEM_ZLIB = "the system's libz (/usr/lib)"
# Changed by hand to rebuild every platform's cached build although nothing pinned here changed
# (for example after a runner image moved to another compiler).
CACHE_SALT = "1"
# The manylinux image both Linux builds run in, of ffmpeg and of the app (AlmaLinux 9, glibc 2.34,
# so the programs run on every Linux with glibc 2.34 or later), pinned by digest.
MANYLINUX_IMAGE = (
    "quay.io/pypa/manylinux_2_34_x86_64@sha256:180e8e234512306be41bb00db574c7df1ceb8af937b10c430b5e2fda402f7da7"
)
# The packages a platform's ffmpeg build installs first: apt on the Ubuntu runner of the Windows
# cross build (the mingw-w64 GCC with Windows threads), Homebrew on macOS (nasm only for x86),
# dnf with the CRB repository in the manylinux image.
BUILD_PACKAGES: Mapping[Platform, tuple[str, ...]] = MappingProxyType(
    {
        "windows-x64": ("gcc-mingw-w64-x86-64-win32", "nasm", "pkg-config"),
        "macos-arm64": (),
        "macos-x64": ("nasm",),
        "linux-x64": ("nasm", "pkgconf-pkg-config"),
    },
)


@dataclass(frozen=True)
class Source:
    """A pinned source tarball, its SHA-256 and the key that signed the release it was taken from."""

    name: str
    version: str
    url: str
    sha256: str
    # Fingerprint of the PGP key whose signature of the tarball was checked when it was pinned.
    signing_key: str
    # The licence file in the source tree, quoted by the notices.
    licence: str

    @property
    def archive(self) -> str:
        """Return the tarball's file name."""
        return self.url.rsplit("/", 1)[-1]

    @property
    def folder(self) -> str:
        """Return the top folder of the tarball."""
        return f"{self.name.lower()}-{self.version}"


# FFmpeg 9.1 "Gauss" (2026-11-20), signed with the FFmpeg release signing key.
FFMPEG = Source(
    name="FFmpeg",
    version="9.1",
    url="https://ffmpeg.org/releases/ffmpeg-9.1.tar.xz",
    sha256="1a4d394ae63d190a526cd052eb4415c156b5c5e2df37efab41a7de0b8fde2433",
    signing_key="FCF986EA15E6E293A5644F10B4322F04D67658D8",
    licence="COPYING.LGPLv2.1",
)
# zlib 1.4, signed with Mark Adler's key; zlib.net keeps every release under `fossils/`.
ZLIB = Source(
    name="zlib",
    version="1.4",
    url="https://zlib.net/fossils/zlib-1.4.tar.gz",
    sha256="02dd159783677a749c04adbdd02b55b8a2bcc7e054f51b76b549199a916ad584",
    signing_key="5ED46A6721D365587791E2AA783FCD8E58BCAFBA",
    licence="LICENSE",
)
# Configure arguments before the thread flag: autodetection off (and the C library's iconv,
# which it leaves on), zlib on.
LEADING_ARGUMENTS = ("--disable-autodetect", "--disable-iconv", "--enable-zlib")
# Configure arguments after the thread flag: static programs, ffmpeg and ffprobe only, no
# documentation, network or device but the `lavfi` input (the media tests generate with it).
COMMON_ARGUMENTS = (
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
)
# Build flags of the macOS builds: the deployment target.
MACOS_ARGUMENTS = (
    f"--extra-cflags=-mmacosx-version-min={MACOS_MINIMUM}",
    f"--extra-ldflags=-mmacosx-version-min={MACOS_MINIMUM}",
)
# Build flags of the Windows build: the mingw-w64 cross build, linked statically so no mingw
# runtime DLL is needed (pkg-config is the build machine's, pointed at the static zlib).
WINDOWS_ARGUMENTS = (
    "--target-os=mingw32",
    "--arch=x86_64",
    f"--cross-prefix={MINGW_PREFIX}",
    "--enable-cross-compile",
    "--pkg-config=pkg-config",
    "--extra-ldflags=-static",
)
# The thread library of each platform, as `configure` names it (Windows' own threads need no
# libwinpthread).
THREADS: Mapping[Platform, str] = MappingProxyType(
    {"linux-x64": "pthreads", "macos-arm64": "pthreads", "macos-x64": "pthreads", WINDOWS: "w32threads"},
)
# The build flags of each platform, after the common arguments.
BUILD_FLAGS: Mapping[Platform, tuple[str, ...]] = MappingProxyType(
    {"linux-x64": (), "macos-arm64": MACOS_ARGUMENTS, "macos-x64": MACOS_ARGUMENTS, WINDOWS: WINDOWS_ARGUMENTS},
)


def system_of(target: Platform) -> str:
    """Return the OS part of a platform (`linux`, `macos`, `windows`)."""
    return target.split("-")[0]


def executable(program: Program, target: Platform) -> str:
    """Return the file name of a program on the platform (`.exe` on Windows)."""
    return f"{program}.exe" if target == WINDOWS else program


def uses_bundled_zlib(target: Platform) -> bool:
    """Return whether the build links its own static zlib (macOS uses the system's `libz`)."""
    return system_of(target) != "macos"


def configure_arguments(target: Platform) -> list[str]:
    """Return FFmpeg's configure arguments for the platform, in the order `-buildconf` shows them."""
    thread = f"--enable-{THREADS[target]}"
    return [*LEADING_ARGUMENTS, thread, *COMMON_ARGUMENTS, *BUILD_FLAGS[target]]


def sources(target: Platform) -> list[Source]:
    """Return the sources a platform's build is made from."""
    return [FFMPEG, ZLIB] if uses_bundled_zlib(target) else [FFMPEG]


def build_info(target: Platform) -> dict[str, object]:
    """Return what the notices and the release notes state about a platform's build."""
    zlib = asdict(ZLIB) if uses_bundled_zlib(target) else SYSTEM_ZLIB
    configure = configure_arguments(target)
    return {"platform": target, "ffmpeg": asdict(FFMPEG), "zlib": zlib, "configure": configure}
