"""The steps of an ffmpeg build (docs/ai/plans/m8.md §1 "ffmpeg"), run by `scripts/build_ffmpeg.py`.

Download and check a pinned source, unpack it, build the static zlib, configure and build FFmpeg
out of tree, check the configure result and the libraries both programs load, and copy the
programs, the licences and the build description to the out folder. Each step raises
`RuntimeError` (or `OSError`) when it fails. Standard library only.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from build_ffmpeg_checks import LIBRARY_TOOLS, build_tree_problems, dependency_problems, linked_libraries
from build_ffmpeg_pins import (
    MINGW_PREFIX,
    PROGRAMS,
    THREADS,
    WINDOWS,
    Platform,
    Source,
    build_info,
    configure_arguments,
    executable,
    sources,
    system_of,
    uses_bundled_zlib,
)

# Environment variables of a step.
type Environment = Mapping[str, str]

# Seconds a quick program (`ldd`, `otool`, `objdump`, a built program's `-buildconf`) may take.
TOOL_TIMEOUT_S = 60
# Seconds a download may take.
DOWNLOAD_TIMEOUT_S = 600
# Bytes read at a time when hashing.
HASH_CHUNK_BYTES = 1024 * 1024
# Name of the build description written next to the programs.
BUILD_INFO = "ffmpeg-build.json"
# Folder of the licence texts next to the programs.
LICENCES_FOLDER = "licences"
# The optimisation zlib uses by default; its configure adds `-fPIC` where the platform needs it.
ZLIB_CFLAGS = "-O3"


@dataclass(frozen=True)
class Work:
    """The folders of one build (absolute paths)."""

    root: Path
    out: Path

    @property
    def downloads(self) -> Path:
        """Return the folder of the tarballs."""
        return self.root / "downloads"

    @property
    def sources(self) -> Path:
        """Return the folder the tarballs are unpacked into."""
        return self.root / "src"

    @property
    def zlib_prefix(self) -> Path:
        """Return where the static zlib is installed."""
        return self.root / "zlib"

    @property
    def tree(self) -> Path:
        """Return FFmpeg's build tree (an out-of-tree build)."""
        return self.root / "ffmpeg-build"


def required_tools(target: Platform) -> list[str]:
    """Return the programs a platform's build runs from `PATH` (besides the C compiler configure finds)."""
    tools = ["curl", "make"]
    if target != "macos-arm64":
        # The x86 assembly.
        tools.append("nasm")
    system = system_of(target)
    if system == "windows":
        tools.extend((f"{MINGW_PREFIX}gcc", f"{MINGW_PREFIX}objdump", "pkg-config"))
    elif system == "macos":
        tools.append("otool")
    else:
        tools.extend(("ldd", "pkg-config"))
    return tools


def missing_tools(target: Platform) -> list[str]:
    """Return the programs of `required_tools` that are not on `PATH`."""
    return [name for name in required_tools(target) if shutil.which(name) is None]


def tool(name: str) -> str:
    """Return the path of a program on `PATH`.

    Raises:
        FileNotFoundError: It is not there.
    """
    found = shutil.which(name)
    if found is None:
        msg = f"{name} is not on PATH"
        raise FileNotFoundError(msg)
    return found


def run_step(argv: Sequence[str], cwd: Path, env: Environment | None = None) -> None:
    """Run a build step with its output shown.

    Raises:
        RuntimeError: It failed.
    """
    sys.stdout.write(f"$ {' '.join(argv)}\n")
    sys.stdout.flush()
    completed = subprocess.run(argv, cwd=cwd, env=env, check=False)
    if completed.returncode:
        msg = f"{argv[0]} failed (exit {completed.returncode})"
        raise RuntimeError(msg)


def output_of(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run a quick program (a check of a built one) and capture its output as text.

    Raises:
        RuntimeError: It ran longer than `TOOL_TIMEOUT_S`.
    """
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=TOOL_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        msg = f"{argv[0]} did not finish within {TOOL_TIMEOUT_S} s"
        raise RuntimeError(msg) from error


def sha256_of(path: Path) -> str:
    """Return the SHA-256 of a file in hex."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(source: Source, downloads: Path) -> Path:
    """Download a source tarball (unless a verified copy is there) and check its SHA-256.

    Raises:
        RuntimeError: The download failed or its SHA-256 is not the pinned one.
    """
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / source.archive
    if archive.is_file() and sha256_of(archive) == source.sha256:
        return archive
    command = [tool("curl"), "--fail", "--location", "--silent", "--show-error", "--retry", "3"]
    command += ["--max-time", str(DOWNLOAD_TIMEOUT_S), "--output", str(archive), source.url]
    run_step(command, downloads)
    found = sha256_of(archive)
    if found != source.sha256:
        archive.unlink()
        msg = f"{source.archive}: SHA-256 {found}, expected {source.sha256}"
        raise RuntimeError(msg)
    return archive


def unpack(archive: Path, source: Source, destination: Path) -> Path:
    """Unpack a verified tarball afresh; return its top folder."""
    folder = destination / source.folder
    shutil.rmtree(folder, ignore_errors=True)
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        tar.extractall(destination, filter="data")
    return folder


def _make() -> list[str]:
    """Return `make` with one job per CPU."""
    return [tool("make"), f"-j{os.cpu_count() or 1}"]


def build_zlib(source_dir: Path, target: Platform, prefix: Path) -> None:
    """Build and install the static zlib (`libz.a`, its headers and `zlib.pc`) into `prefix`."""
    environment = {**os.environ, "CFLAGS": ZLIB_CFLAGS}
    if target == WINDOWS:
        # The configure of zlib takes the prefix of the cross tools from `CHOST`.
        environment["CHOST"] = MINGW_PREFIX.rstrip("-")
    shutil.rmtree(prefix, ignore_errors=True)
    run_step(["./configure", "--static", f"--prefix={prefix}"], source_dir, environment)
    run_step(_make(), source_dir, environment)
    run_step([tool("make"), "install"], source_dir, environment)


def configure_environment(target: Platform, work: Work) -> dict[str, str]:
    """Return the environment of FFmpeg's configure: pkg-config sees only the static zlib (or nothing)."""
    environment = dict(os.environ)
    environment.pop("PKG_CONFIG_PATH", None)
    libdir = work.zlib_prefix / "lib" / "pkgconfig"
    if not uses_bundled_zlib(target):
        # An empty folder: the system's libz is found without pkg-config, a Homebrew one never.
        libdir = work.root / "no-pkgconfig"
        libdir.mkdir(parents=True, exist_ok=True)
    environment["PKG_CONFIG_LIBDIR"] = str(libdir)
    return environment


def build_ffmpeg(source_dir: Path, target: Platform, work: Work) -> None:
    """Configure FFmpeg with the platform's line and build it out of tree."""
    shutil.rmtree(work.tree, ignore_errors=True)
    work.tree.mkdir(parents=True)
    environment = configure_environment(target, work)
    configure = [str(source_dir / "configure"), *configure_arguments(target)]
    run_step(configure, work.tree, environment)
    run_step(_make(), work.tree, environment)


def _library_listing(program: Path, target: Platform) -> str:
    system = system_of(target)
    if system == "windows":
        command = [tool(f"{MINGW_PREFIX}objdump"), "-p", str(program)]
    elif system == "macos":
        command = [tool("otool"), "-L", str(program)]
    else:
        command = [tool("ldd"), str(program)]
    completed = output_of(command)
    if completed.returncode:
        msg = f"{LIBRARY_TOOLS[system]} failed for {program}: {completed.stderr}"
        raise RuntimeError(msg)
    return completed.stdout


def check_build_tree(source_dir: Path, target: Platform, work: Work) -> list[str]:
    """Return the problems of the configure result and of the libraries both programs load."""
    configure = (source_dir / "configure").read_text(encoding="utf-8")
    config_mak = (work.tree / "ffbuild" / "config.mak").read_text(encoding="utf-8")
    problems = build_tree_problems(configure, config_mak, THREADS[target])
    system = system_of(target)
    for program in PROGRAMS:
        listing = _library_listing(work.tree / executable(program, target), target)
        problems.extend(dependency_problems(program, system, linked_libraries(system, listing)))
    return problems


def out_problem(out: Path) -> str | None:
    """Return why the out folder must not be replaced, or None.

    It is emptied before the programs are copied, so it must be missing, empty or an earlier
    build's (holding its `ffmpeg-build.json`): a mistyped `--out` never deletes other files.
    """
    if not out.exists():
        return None
    if not out.is_dir():
        return f"{out} is not a folder"
    if any(out.iterdir()) and not (out / BUILD_INFO).is_file():
        return f"{out} holds files but no {BUILD_INFO}: not an ffmpeg build, so it is not replaced"
    return None


def publish(source_dirs: Mapping[str, Path], target: Platform, out: Path, tree: Path) -> None:
    """Copy both programs from the build tree, the licences and the build description into `out`.

    Raises:
        RuntimeError: `out` holds something other than an earlier build (`out_problem`).
    """
    refused = out_problem(out)
    if refused is not None:
        raise RuntimeError(refused)
    shutil.rmtree(out, ignore_errors=True)
    licences = out / LICENCES_FOLDER
    licences.mkdir(parents=True)
    for program in PROGRAMS:
        shutil.copy(tree / executable(program, target), out)
    for source in sources(target):
        licence = source_dirs[source.name] / source.licence
        copy = licences / f"{source.name}-{source.licence}"
        shutil.copy(licence, copy)
    description = json.dumps(build_info(target), indent=2)
    (out / BUILD_INFO).write_text(f"{description}\n", encoding="utf-8")
