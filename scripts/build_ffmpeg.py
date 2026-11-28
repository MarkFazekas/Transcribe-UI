"""Build the static LGPL ffmpeg and ffprobe the desktop app bundles (docs/ai/plans/m8.md §1 "ffmpeg").

Usage, from the repository root (standard library only; `curl`, `make` and a C toolchain on `PATH`):

    uv run --no-project scripts/build_ffmpeg.py key --platform <tag>   # the cache key, for $GITHUB_OUTPUT
    uv run --no-project scripts/build_ffmpeg.py build [--platform <tag>] [--out DIR] [--work DIR]
    uv run --no-project scripts/build_ffmpeg.py verify [--platform <tag>] [--first-on-path] DIR
    uv run --no-project scripts/build_ffmpeg.py configuration           # sources and configure lines (JSON)

`<tag>` is a release platform (`windows-x64`, `macos-arm64`, `macos-x64`, `linux-x64`; default:
this machine's). `build` downloads the pinned sources (`build_ffmpeg_pins.py`) and checks their
SHA-256, builds zlib statically (not on macOS, which uses the system's), configures FFmpeg with
the platform's line and builds it, checks the build tree's configure result and the libraries
both programs load (`build_ffmpeg_checks.py`), and writes `ffmpeg`, `ffprobe` (`.exe` on
Windows), the licences and `ffmpeg-build.json` (sources and configure line) to `--out` (default
`build/ffmpeg/dist`, which `scripts/build_desktop.py` bundles). Windows is cross-compiled on
Linux with mingw-w64 (`x86_64-w64-mingw32-gcc`, Windows threads); the x86 builds need `nasm`.
`verify` runs a build's programs: `-buildconf` must equal the platform's configure line and `-L`
must report LGPL 2.1 or later; with `--first-on-path`, `PATH` must find both programs in DIR.
The cache key hashes every `build_ffmpeg*.py`, so any change to the build or its checks
rebuilds.
"""

import argparse
import hashlib
import json
import platform
import shutil
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from build_desktop import platform_tag
from build_ffmpeg_checks import buildconf_arguments, buildconf_problems, licence_problems
from build_ffmpeg_pins import (
    FFMPEG,
    PLATFORMS,
    PROGRAMS,
    ZLIB,
    Platform,
    build_info,
    configure_arguments,
    executable,
    sources,
    uses_bundled_zlib,
)
from build_ffmpeg_steps import (
    Work,
    build_ffmpeg,
    build_zlib,
    check_build_tree,
    fetch,
    missing_tools,
    output_of,
    publish,
    unpack,
)

# Folder of the scripts.
SCRIPTS_DIR = Path(__file__).resolve().parent
# Root of the repository.
REPO_ROOT = SCRIPTS_DIR.parent
# The files whose content decides the build and its checks; the cache key hashes them.
KEYED_FILES = tuple(sorted(SCRIPTS_DIR.glob("build_ffmpeg*.py")))
# Characters of the files' hash in the cache key.
KEY_HASH_LENGTH = 16
# Default folder of the built programs (the cached folder).
DEFAULT_OUT = REPO_ROOT / "build" / "ffmpeg" / "dist"
# Default work folder (downloads, sources, the zlib install, the build tree).
DEFAULT_WORK = REPO_ROOT / "build" / "ffmpeg" / "work"
# Bytes in a megabyte, for the report.
MEGABYTE = 1_000_000


def cache_key(target: Platform, files: Sequence[Path] = KEYED_FILES) -> str:
    """Return the cache key of a platform's build: FFmpeg's version, the platform and a hash of the build files."""
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.read_bytes())
    short = digest.hexdigest()[:KEY_HASH_LENGTH]
    return f"ffmpeg-{FFMPEG.version}-{target}-{short}"


def build(target: Platform, work: Work) -> dict[str, float]:
    """Build both programs for the platform into `work.out`; return the seconds of the zlib and FFmpeg builds.

    Raises:
        RuntimeError: A tool is missing, a step failed, or the build breaks the allow-list.
    """
    missing = missing_tools(target)
    if missing:
        msg = f"the {target} build needs {', '.join(missing)} on PATH"
        raise RuntimeError(msg)
    folders = {}
    for source in sources(target):
        archive = fetch(source, work.downloads)
        folders[source.name] = unpack(archive, source, work.sources)
    timings: dict[str, float] = {}
    started = time.monotonic()
    if uses_bundled_zlib(target):
        build_zlib(folders[ZLIB.name], target, work.zlib_prefix)
        timings["zlib"] = time.monotonic() - started
    started = time.monotonic()
    build_ffmpeg(folders[FFMPEG.name], target, work)
    timings["ffmpeg"] = time.monotonic() - started
    problems = check_build_tree(folders[FFMPEG.name], target, work)
    if problems:
        raise RuntimeError("\n".join(problems))
    publish(folders, target, work.out, work.tree)
    return timings


def _program_problems(path: Path, target: Platform) -> list[str]:
    """Return how one built program's `-buildconf` and `-L` differ from what the platform's build must show."""
    shown = output_of([str(path), "-hide_banner", "-buildconf"])
    arguments = buildconf_arguments(shown.stdout + shown.stderr)
    problems = buildconf_problems(path.name, arguments, configure_arguments(target))
    licence = output_of([str(path), "-hide_banner", "-L"])
    problems.extend(licence_problems(path.name, licence.stdout))
    return problems


def verify(target: Platform, folder: Path, *, first_on_path: bool) -> list[str]:
    """Run the built programs: `-buildconf` equals the platform's configure line, `-L` reports LGPL 2.1+."""
    problems: list[str] = []
    for program in PROGRAMS:
        path = folder / executable(program, target)
        if not path.is_file():
            problems.append(f"missing {path}")
            continue
        problems.extend(_program_problems(path, target))
        found = shutil.which(program)
        if first_on_path and (found is None or not path.samefile(found)):
            problems.append(f"PATH finds {found} for {program}, not {path}")
    return problems


def report(target: Platform, out: Path, timings: dict[str, float]) -> str:
    """Return the line that states the build: its programs' sizes and the seconds of each step."""
    parts = [_size(out / executable(program, target)) for program in PROGRAMS]
    for title, seconds in timings.items():
        parts.append(f"{title} build {seconds:.0f} s")
    built = f"Built FFmpeg {FFMPEG.version} for {target} into {out}"
    return f"{built}: {', '.join(parts)}\n"


def _size(path: Path) -> str:
    megabytes = path.stat().st_size / MEGABYTE
    return f"{path.name} {megabytes:.1f} MB"


def host_platform() -> Platform:
    """Return this machine's release platform.

    Raises:
        ValueError: It is not one the release builds for.
    """
    tag = platform_tag(sys.platform, platform.machine())
    for target in PLATFORMS:
        if target == tag:
            return target
    msg = f"no ffmpeg build for {tag}"
    raise ValueError(msg)


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(description="Build the static LGPL ffmpeg and ffprobe of the desktop app.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("configuration", help="print the sources and configure line of every platform (JSON)")
    key = commands.add_parser("key", help="print the cache key of a platform's build")
    build_command = commands.add_parser("build", help="build both programs")
    verify_command = commands.add_parser("verify", help="run a build's programs")
    for command in (key, build_command, verify_command):
        command.add_argument("--platform", choices=PLATFORMS, help="the release platform (default: this machine's)")
    build_command.add_argument("--out", type=Path, default=DEFAULT_OUT, help="folder of the programs")
    build_command.add_argument("--work", type=Path, default=DEFAULT_WORK, help="work folder")
    verify_command.add_argument("folder", type=Path, help="folder of the programs")
    verify_command.add_argument("--first-on-path", action="store_true", help="PATH must find both programs there")
    return parser.parse_args(argv)


def _build_command(target: Platform, args: argparse.Namespace) -> int:
    work = Work(args.work.resolve(), args.out.resolve())
    try:
        timings = build(target, work)
    except (RuntimeError, OSError) as error:
        sys.stderr.write(f"{error}\n")
        return 1
    sys.stdout.write(report(target, work.out, timings))
    return 0


def _verify_command(target: Platform, args: argparse.Namespace) -> int:
    folder = args.folder.resolve()
    problems = verify(target, folder, first_on_path=args.first_on_path)
    for problem in problems:
        sys.stderr.write(f"{problem}\n")
    if problems:
        return 1
    sys.stdout.write(f"ffmpeg and ffprobe in {folder} are the LGPL build of {target}\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command; return the exit code."""
    args = parse_arguments(argv)
    if args.command == "configuration":
        every = {target: build_info(target) for target in PLATFORMS}
        sys.stdout.write(f"{json.dumps(every, indent=2)}\n")
        return 0
    target: Platform = args.platform or host_platform()
    if args.command == "key":
        # Bytes, so Windows does not end the line with a carriage return (it goes to `$GITHUB_OUTPUT`).
        sys.stdout.buffer.write(f"key={cache_key(target)}\n".encode())
        return 0
    if args.command == "build":
        return _build_command(target, args)
    return _verify_command(target, args)


if __name__ == "__main__":
    sys.exit(main())
