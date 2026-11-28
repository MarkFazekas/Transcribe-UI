"""Build the desktop executables with PyInstaller (docs/ai/plans/m8.md §1 "Packaging tool").

Usage, from the repository root:

    uv run --no-project scripts/build_desktop.py stamp >> "$GITHUB_OUTPUT"
    uv sync --locked --no-dev --no-editable --package transcribe-ui --group packaging
    uv run --no-sync python scripts/build_desktop.py build --version <stamped> [--ffmpeg-dir DIR] [--variant cli|gui ...]

`stamp` prints `version=<base>+<YYYYMMDD>.<short sha>` and `tag=desktop-<YYYYMMDD>-<short sha>`
for the checked-out commit (its committer date in UTC, so every leg and any re-run agree); it
needs only git and the standard library. `build` runs in the build environment (PyInstaller and
the app installed as a non-editable distribution) and writes one one-file executable per variant
to `dist/`, named `transcribe-ui-<os>-<arch>-<variant>` (`.exe` on Windows), the name a release
publishes. Both bundle ffmpeg and ffprobe from `--ffmpeg-dir` (default `build/ffmpeg/dist`,
where `scripts/build_ffmpeg.py build` writes them) at the archive's root, where the app looks for
them (`media/tools.py` `bundle_dir`). The stamped version is bundled as
`transcribe_ui/version.txt`; on Windows a generated version resource describes the file and the
app icon (`apps/desktop/packaging/icon/`) is the file's icon. Each executable bundles its
third-party notices as `transcribe_ui/THIRD_PARTY_NOTICES.txt`, made inside PyInstaller from
what it collected (`build_desktop_facts.py`); a collected file without a licence entry fails
the build. No `.spec` file is kept: PyInstaller's is written to the work folder.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import build_desktop_facts
from build_desktop_macos import check_bundle
from build_platform import host_tag

# Root of the repository.
REPO_ROOT = Path(__file__).resolve().parent.parent
# The desktop app's workspace member.
DESKTOP_DIR = REPO_ROOT / "apps" / "desktop"
# The desktop app's manifest; its `[project] version` is the base version.
DESKTOP_MANIFEST = DESKTOP_DIR / "pyproject.toml"
# Packaging files: entry scripts, the PyInstaller runner and the project hooks.
PACKAGING_DIR = DESKTOP_DIR / "packaging"
# Project hooks of PyInstaller (`--additional-hooks-dir`).
HOOKS_DIR = PACKAGING_DIR / "hooks"
# Runs PyInstaller with the Linux build's library exclusions.
PYINSTALLER_RUNNER = PACKAGING_DIR / "pyinstaller_main.py"
# The app icon's source and the files made from it (`scripts/build_icon.py`).
ICON_DIR = PACKAGING_DIR / "icon"
# The Windows executables' icon.
WINDOWS_ICON = ICON_DIR / "transcribe-ui.ico"
# The macOS `.app`'s icon (the onedir `.app` of M8.4 takes it; a bare one-file binary has none).
MACOS_ICON = ICON_DIR / "transcribe-ui.icns"
# Environment variable naming the notices configuration the PyInstaller runner reads.
NOTICES_VARIABLE = "TRANSCRIBE_UI_NOTICES_CONFIG"
# Default folder of the built executables.
DEFAULT_DIST = REPO_ROOT / "dist"
# Default work folder of PyInstaller (its analysis, the generated spec and resource files).
DEFAULT_WORK = REPO_ROOT / "build" / "desktop"
# Default folder of the ffmpeg build both executables bundle (`scripts/build_ffmpeg.py build`).
DEFAULT_FFMPEG_DIR = REPO_ROOT / "build" / "ffmpeg" / "dist"

# An executable of the app: the windowed GUI with Qt, or the console CLI without Qt.
type Variant = Literal["cli", "gui"]
# The variants in build order.
VARIANTS: tuple[Variant, ...] = ("cli", "gui")
# Name of the app's distribution and of the executables' stem.
APP_NAME = "transcribe-ui"
# The app's top-level package; the stamp is bundled as its data.
PACKAGE_NAME = "transcribe_ui"
# Name of the stamp inside the package (`transcribe_ui.constants.VERSION_STAMP_FILE`).
VERSION_STAMP_FILE = "version.txt"
# Environment variable the project hook reads to tell the variants apart.
VARIANT_VARIABLE = "TRANSCRIBE_UI_BUNDLE_VARIANT"
# Characters of the commit hash in the stamped version and the tag.
SHORT_SHA_LENGTH = 7
# Components of a numeric Windows file version.
WINDOWS_VERSION_PARTS = 4
# Leading release segment of a PEP 440 version (`0.1.0` of a release candidate of it).
RELEASE_SEGMENT = re.compile(r"\d+(?:\.\d+)*")
# Product name in the Windows version resource.
PRODUCT_NAME = "Transcribe-UI"
# File description of the CLI executable in the Windows version resource.
CLI_FILE_DESCRIPTION = "Transcribe-UI command line"
# File description of the GUI executable in the Windows version resource.
GUI_FILE_DESCRIPTION = "Transcribe-UI"
# Python modules the CLI never contains (it must never import Qt or the GUI).
CLI_EXCLUDED_MODULES = ("PySide6", "shiboken6", "transcribe_ui.gui")
# Qt modules the GUI does not use but a hook could pull in. Qt Network brings the TLS plugins,
# whose OpenSSL backend loads libssl from wherever the build machine has one (on Windows a
# tool folder on PATH); the app makes no network connection through Qt (boto3 uses Python's ssl).
GUI_EXCLUDED_MODULES = ("PySide6.QtTest", "PySide6.QtNetwork")
# The media tools both executables bundle at the archive's root.
MEDIA_TOOLS = ("ffmpeg", "ffprobe")
# Bytes in a megabyte, for the build's report.
MEGABYTE = 1_000_000


@dataclass(frozen=True)
class Commit:
    """The checked-out commit: its full hash and committer time."""

    sha: str
    committed: datetime

    @property
    def short_sha(self) -> str:
        """Return the hash as written in versions and tags."""
        return self.sha[:SHORT_SHA_LENGTH]

    @property
    def day(self) -> str:
        """Return the committer date in UTC as `YYYYMMDD`."""
        return self.committed.astimezone(UTC).strftime("%Y%m%d")


@dataclass(frozen=True)
class BuildPaths:
    """Where one variant's inputs and outputs go (absolute paths)."""

    dist: Path
    work: Path
    # Folder of the ffmpeg build to bundle.
    ffmpeg: Path

    @property
    def stamp(self) -> Path:
        """Return the generated stamp file."""
        return self.work / VERSION_STAMP_FILE

    @property
    def notices(self) -> Path:
        """Return the generated third-party notices."""
        return self.work / "THIRD_PARTY_NOTICES.txt"

    @property
    def notices_config(self) -> Path:
        """Return the notices configuration the PyInstaller runner reads."""
        return self.work / "notices.json"

    @property
    def version_resource(self) -> Path:
        """Return the generated Windows version resource."""
        return self.work / "version-resource.txt"

    @classmethod
    def from_arguments(cls, args: argparse.Namespace) -> "BuildPaths":
        """Return the build's folders from the command line (the work folder holds one folder per variant)."""
        dist = args.dist.resolve()
        work = args.work.resolve()
        return cls(dist, work, args.ffmpeg_dir.resolve())

    def media_tool(self, tool: str, tag: str) -> Path:
        """Return ffmpeg's or ffprobe's executable of the platform `tag` in the ffmpeg build (`.exe` on Windows)."""
        suffix = ".exe" if is_windows(tag) else ""
        return self.ffmpeg / f"{tool}{suffix}"


def stamped_version(base: str, commit: Commit) -> str:
    """Return a release build's version, `<base>+<YYYYMMDD>.<short sha>` (a PEP 440 local version)."""
    return f"{base}+{commit.day}.{commit.short_sha}"


def release_tag(commit: Commit) -> str:
    """Return the tag of a release of the commit, `desktop-<YYYYMMDD>-<short sha>`."""
    return f"desktop-{commit.day}-{commit.short_sha}"


def is_windows(tag: str) -> bool:
    """Return whether the platform tag is a Windows one."""
    return tag.startswith("windows-")


def asset_stem(variant: Variant, tag: str) -> str:
    """Return the executable's name without the Windows suffix (PyInstaller's `--name`)."""
    return f"{APP_NAME}-{tag}-{variant}"


def asset_name(variant: Variant, tag: str) -> str:
    """Return the file name of the executable, as published: `transcribe-ui-<os>-<arch>-<variant>[.exe]`."""
    suffix = ".exe" if is_windows(tag) else ""
    return f"{asset_stem(variant, tag)}{suffix}"


def numeric_version(base: str) -> tuple[int, ...]:
    """Return the four numbers of a Windows file version from the base version's release segment.

    Raises:
        ValueError: The version does not start with a release segment.
    """
    match = RELEASE_SEGMENT.match(base)
    if match is None:
        msg = f"{base!r} has no release segment"
        raise ValueError(msg)
    parts = [int(part) for part in match.group().split(".")]
    padded = (*parts, 0, 0, 0, 0)
    return padded[:WINDOWS_VERSION_PARTS]


def _string_struct(key: str, text: str) -> str:
    return f"          StringStruct({key!r}, {text!r})"


def windows_version_resource(variant: Variant, base: str, version: str, tag: str) -> str:
    """Return PyInstaller's `--version-file` text of the executable (shown in the file's properties)."""
    numbers = numeric_version(base)
    description = CLI_FILE_DESCRIPTION if variant == "cli" else GUI_FILE_DESCRIPTION
    strings = (
        ("CompanyName", PRODUCT_NAME),
        ("FileDescription", description),
        ("FileVersion", base),
        ("InternalName", asset_stem(variant, tag)),
        ("OriginalFilename", asset_name(variant, tag)),
        ("ProductName", PRODUCT_NAME),
        ("ProductVersion", version),
    )
    entries = ",\n".join(_string_struct(*entry) for entry in strings)
    return (
        "VSVersionInfo(\n"
        "  ffi=FixedFileInfo(\n"
        f"    filevers={numbers},\n"
        f"    prodvers={numbers},\n"
        "    mask=0x3F,\n"
        "    flags=0x0,\n"
        "    OS=0x40004,\n"
        "    fileType=0x1,\n"
        "    subtype=0x0,\n"
        "    date=(0, 0),\n"
        "  ),\n"
        "  kids=[\n"
        "    StringFileInfo([\n"
        "      StringTable(\n"
        "        '040904B0',\n"
        "        [\n"
        f"{entries},\n"
        "        ],\n"
        "      ),\n"
        "    ]),\n"
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])]),\n"
        "  ],\n"
        ")\n"
    )


def pyinstaller_arguments(variant: Variant, tag: str, paths: BuildPaths) -> list[str]:
    """Return PyInstaller's command line for one variant on the platform `tag`."""
    arguments = [
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noupx",
        "--name",
        asset_stem(variant, tag),
        "--distpath",
        str(paths.dist),
        "--workpath",
        str(paths.work / "build"),
        "--specpath",
        str(paths.work),
        "--additional-hooks-dir",
        str(HOOKS_DIR),
        "--copy-metadata",
        APP_NAME,
        "--copy-metadata",
        "keyring",
        "--collect-submodules",
        "keyring.backends",
        "--add-data",
        f"{paths.stamp}:{PACKAGE_NAME}",
    ]
    for tool in MEDIA_TOOLS:
        arguments.extend(("--add-binary", f"{paths.media_tool(tool, tag)}:."))
    excluded = CLI_EXCLUDED_MODULES if variant == "cli" else GUI_EXCLUDED_MODULES
    for module in excluded:
        arguments.extend(("--exclude-module", module))
    # Windowed only on Windows; the macOS GUI stays a console-mode one-file binary until its shape
    # is decided (an open architect question).
    windowed = variant == "gui" and is_windows(tag)
    arguments.append("--windowed" if windowed else "--console")
    if is_windows(tag):
        resource, icon = str(paths.version_resource), str(WINDOWS_ICON)
        arguments.extend(("--version-file", resource, "--icon", icon))
    arguments.append(str(PACKAGING_DIR / f"entry_{variant}.py"))
    return arguments


def base_version(manifest: Path = DESKTOP_MANIFEST) -> str:
    """Return `[project] version` of the desktop app's manifest."""
    with manifest.open("rb") as stream:
        project = tomllib.load(stream)["project"]
    version = project["version"]
    if not isinstance(version, str):
        msg = f"{manifest}: [project] version is not a string"
        raise TypeError(msg)
    return version


def head_commit() -> Commit:
    """Return the checked-out commit (git must be on `PATH`)."""
    git = shutil.which("git")
    if git is None:
        msg = "git is not on PATH"
        raise FileNotFoundError(msg)
    completed = subprocess.run(
        [git, "show", "--no-patch", "--format=%H %ct", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    sha, timestamp = completed.stdout.split()
    return Commit(sha, datetime.fromtimestamp(int(timestamp), UTC))


def _write_notices_config(paths: BuildPaths, title: str) -> None:
    """Write the configuration the PyInstaller runner makes the third-party notices from."""
    own = (PACKAGING_DIR, paths.work)
    build_desktop_facts.write_config(
        paths.notices_config, output=paths.notices, title=title, ffmpeg=paths.ffmpeg, own=own
    )


def build_variant(variant: Variant, version: str, paths: BuildPaths) -> Path:
    """Build one variant into `paths.dist`; return the executable.

    Raises:
        RuntimeError: PyInstaller failed or did not write the executable.
    """
    tag = host_tag()
    present = [paths.media_tool(tool, tag).is_file() for tool in MEDIA_TOOLS]
    if not all(present):
        msg = f"{paths.ffmpeg} lacks ffmpeg or ffprobe (build them with scripts/build_ffmpeg.py build)"
        raise RuntimeError(msg)
    shutil.rmtree(paths.work, ignore_errors=True)
    paths.work.mkdir(parents=True)
    paths.stamp.write_text(f"{version}\n", encoding="utf-8")
    if is_windows(tag):
        resource = windows_version_resource(variant, base_version(), version, tag)
        paths.version_resource.write_text(resource, encoding="utf-8")
    name = asset_name(variant, tag)
    _write_notices_config(paths, f"{PRODUCT_NAME} {version} ({name})")
    arguments = pyinstaller_arguments(variant, tag, paths)
    command = [sys.executable, str(PYINSTALLER_RUNNER), *arguments]
    environment = {**os.environ, VARIANT_VARIABLE: variant, NOTICES_VARIABLE: str(paths.notices_config)}
    sys.stdout.write(f"Building the {variant} executable {name} ({version})\n")
    sys.stdout.flush()
    completed = subprocess.run(command, cwd=REPO_ROOT, env=environment, check=False)
    if completed.returncode:
        msg = f"PyInstaller failed for the {variant} executable (exit {completed.returncode})"
        raise RuntimeError(msg)
    executable = paths.dist / asset_name(variant, tag)
    if not executable.is_file():
        msg = f"PyInstaller did not write {executable}"
        raise RuntimeError(msg)
    return executable


def _stamp() -> int:
    commit = head_commit()
    version = stamped_version(base_version(), commit)
    lines = f"version={version}\ntag={release_tag(commit)}\n"
    # Bytes, so Windows does not end the lines with a carriage return (they go to `$GITHUB_OUTPUT`).
    sys.stdout.buffer.write(lines.encode())
    sys.stdout.flush()
    return 0


def _macos_floor_problems(variant: Variant, paths: BuildPaths, executable: Path) -> list[str]:
    """Return what in the executable needs a newer macOS than the release supports; print each wheel's line."""
    stem = asset_stem(variant, host_tag())
    report = check_bundle(paths.work / "build" / stem / "PKG-00.toc", executable)
    for line in report.wheels:
        sys.stdout.write(f"{line}\n")
    return list(report.problems)


def _build(variants: Sequence[Variant], version: str, folders: BuildPaths) -> int:
    # The interpreter decides what is bundled (its libpython, its minimum OS): name it in the log.
    sys.stdout.write(f"Build interpreter: {sys.executable}, Python {sys.version}\n")
    # The third-party notices judge the libraries inside libpython by these (build_desktop_licences.py).
    builtin_modules = " ".join(sorted(sys.builtin_module_names))
    sys.stdout.write(f"Built-in modules: {builtin_modules}\n")
    for variant in variants:
        paths = replace(folders, work=folders.work / variant)
        try:
            executable = build_variant(variant, version, paths)
            problems = _macos_floor_problems(variant, paths, executable) if sys.platform == "darwin" else []
        except (RuntimeError, OSError, subprocess.SubprocessError) as error:
            sys.stderr.write(f"{error}\n")
            return 1
        if problems:
            report = "\n".join(problems)
            sys.stderr.write(f"{report}\n")
            return 1
        size_mb = executable.stat().st_size / MEGABYTE
        sys.stdout.write(f"Built {executable} ({size_mb:.1f} MB)\n")
    return 0


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(description="Build the desktop executables with PyInstaller.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("stamp", help="print the stamped version and the release tag of the checked-out commit")
    build = commands.add_parser("build", help="build the executables")
    build.add_argument("--version", required=True, help="the stamped version (`stamp` prints it)")
    build.add_argument("--variant", action="append", choices=VARIANTS, help="variant to build (default: both)")
    build.add_argument("--dist", type=Path, default=DEFAULT_DIST, help="folder of the executables")
    build.add_argument("--work", type=Path, default=DEFAULT_WORK, help="PyInstaller's work folder")
    tools_help = "folder of ffmpeg and ffprobe (scripts/build_ffmpeg.py build)"
    build.add_argument("--ffmpeg-dir", type=Path, default=DEFAULT_FFMPEG_DIR, help=tools_help)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command; return the exit code."""
    args = parse_arguments(argv)
    if args.command == "stamp":
        return _stamp()
    variants: Sequence[Variant] = args.variant or VARIANTS
    return _build(variants, args.version, BuildPaths.from_arguments(args))


if __name__ == "__main__":
    sys.exit(main())
