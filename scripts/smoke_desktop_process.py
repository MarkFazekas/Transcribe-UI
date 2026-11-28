"""Processes of the desktop smoke test: start with a timeout, kill the whole tree, isolate state.

Used by `scripts/smoke_desktop_build.py`. A one-file executable is two processes (PyInstaller's
bootloader and the app it unpacks and starts), `xvfb-run` adds an X server, so a process that
times out is killed with everything it started: on POSIX its own session (it is started as a
session leader), on Windows its tree (`taskkill /T`). Output goes to files, not pipes, so a
grandchild that keeps a pipe open cannot block the wait.
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol, TypedDict

# A command line.
type Command = Sequence[str]
# Environment variables.
type Environment = Mapping[str, str]
# A started process whose output goes to files.
type Process = subprocess.Popen[bytes]
# How a process ended: its exit code (None: killed after its timeout) and how long it ran, in seconds.
type Ending = tuple[int | None, float]

# Programs whose folders are dropped from `PATH`, so a build cannot fall back on the runner's copy.
MEDIA_TOOLS = ("ffmpeg", "ffprobe")
# Variables that would change what the app does or reads; never passed to it.
DROPPED_VARIABLES = frozenset(
    (
        "QT_QPA_PLATFORM",
        "TRANSCRIBE_UI_SMOKE_TEST",
        "LANGUAGE",
        "LC_ALL",
        "LC_MESSAGES",
        "R2_ENDPOINT_URL",
        "R2_BUCKET",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "RUNPOD_API_KEY",
    ),
)
# Variables that point platformdirs at the fresh state on POSIX, and their folders.
POSIX_STATE = (
    ("HOME", "home"),
    ("XDG_DATA_HOME", "data"),
    ("XDG_CONFIG_HOME", "config"),
    ("XDG_CACHE_HOME", "cache"),
    ("XDG_STATE_HOME", "state"),
)
# Variables that point platformdirs at the fresh state on Windows, and their folders.
WINDOWS_STATE = (
    ("WIN_PD_OVERRIDE_APPDATA", "Roaming"),
    ("WIN_PD_OVERRIDE_LOCAL_APPDATA", "Local"),
)
# Width argparse wraps `--help` at, so a help text is not broken across lines.
HELP_COLUMNS = "200"
# Seconds to wait for a killed process tree to be gone.
KILL_WAIT_S = 30
# Seconds the Windows tree kill may take.
TREE_KILL_TIMEOUT_S = 60


@dataclass(frozen=True)
class RunResult:
    """What a finished (or killed) process left: its exit code (None: timed out), output and duration."""

    returncode: int | None
    stdout: bytes
    stderr: bytes
    seconds: float

    def text(self) -> str:
        """Return stdout and stderr as text, for a failure report."""
        out = self.stdout.decode("utf-8", errors="replace").rstrip()
        err = self.stderr.decode("utf-8", errors="replace").rstrip()
        return f"--- stdout\n{out}\n--- stderr\n{err}"


class Launcher(Protocol):
    """Runs a command where the executables live (on the host or in a container)."""

    def run(self, argv: Command, extra_env: Environment, timeout_s: float, stdin: bytes = b"") -> RunResult:
        """Run `argv` with `extra_env` added; kill it with everything it started after `timeout_s`."""


def path_without_tools(path_value: str, tools: Sequence[str] = MEDIA_TOOLS) -> str:
    """Return `PATH` without the folders that hold one of `tools` (with any Windows suffix)."""
    folders = [folder for folder in path_value.split(os.pathsep) if folder]
    kept = [folder for folder in folders if _lacks_tools(folder, tools)]
    return os.pathsep.join(kept)


def _lacks_tools(folder: str, tools: Sequence[str]) -> bool:
    found = (shutil.which(tool, path=folder) for tool in tools)
    return all(path is None for path in found)


def isolated_environment(base: Environment, state_root: Path) -> dict[str, str]:
    """Return the environment of the executables: fresh per-user state under `state_root`, no media tools.

    platformdirs finds the state through `HOME` and the XDG variables on POSIX, and through its
    `WIN_PD_OVERRIDE_*` variables on Windows.
    """
    kept = [name for name in base if name.upper() not in DROPPED_VARIABLES]
    environment = {name: base[name] for name in kept}
    environment["PATH"] = path_without_tools(base.get("PATH", ""))
    environment["COLUMNS"] = HELP_COLUMNS
    state = WINDOWS_STATE if sys.platform == "win32" else POSIX_STATE
    for name, folder in state:
        location = state_root / folder
        location.mkdir(parents=True, exist_ok=True)
        environment[name] = str(location)
    return environment


def kill_tree(process: Process) -> None:
    """Kill the process and everything it started; wait until it is gone (at most `KILL_WAIT_S`)."""
    if sys.platform == "win32":
        taskkill = shutil.which("taskkill") or "taskkill"
        command = [taskkill, "/F", "/T", "/PID", str(process.pid)]
        try:
            subprocess.run(command, capture_output=True, timeout=TREE_KILL_TIMEOUT_S, check=False)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        try:
            # The process leads its own session, so its group holds everything it started.
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            process.kill()
    try:
        process.wait(KILL_WAIT_S)
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"process {process.pid} is still running after the kill\n")


class Streams(TypedDict):
    """The environment and the files a process reads and writes (keyword arguments of `Popen`)."""

    env: Environment
    stdin: IO[bytes]
    stdout: IO[bytes]
    stderr: IO[bytes]


def _start(argv: Command, streams: Streams) -> Process:
    if sys.platform == "win32":
        return subprocess.Popen(argv, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, **streams)
    return subprocess.Popen(argv, start_new_session=True, **streams)


def _wait(process: Process, timeout_s: float) -> int | None:
    try:
        return process.wait(timeout_s)
    except subprocess.TimeoutExpired:
        kill_tree(process)
    return None


def _run_in(folder: Path, argv: Command, env: Environment, timeout_s: float) -> Ending:
    """Run `argv` with its input and output in the files of `folder`; return the exit code and the duration."""
    with ExitStack() as files:
        streams = Streams(
            env=env,
            stdin=files.enter_context((folder / "stdin").open("rb")),
            stdout=files.enter_context((folder / "stdout").open("wb")),
            stderr=files.enter_context((folder / "stderr").open("wb")),
        )
        started = time.monotonic()
        returncode = _wait(_start(argv, streams), timeout_s)
        return returncode, time.monotonic() - started


def run_process(argv: Command, env: Environment, timeout_s: float, stdin: bytes = b"") -> RunResult:
    """Run `argv`; after `timeout_s` kill it with everything it started (exit code None)."""
    # A process the kill missed may still hold a file open on Windows; that must not end the smoke test.
    with tempfile.TemporaryDirectory(prefix="smoke-io-", ignore_cleanup_errors=True) as name:
        folder = Path(name)
        (folder / "stdin").write_bytes(stdin)
        returncode, seconds = _run_in(folder, argv, env, timeout_s)
        stdout = (folder / "stdout").read_bytes()
        stderr = (folder / "stderr").read_bytes()
    return RunResult(returncode, stdout, stderr, seconds)


@dataclass(frozen=True)
class HostLauncher:
    """Runs commands on this machine with the isolated environment."""

    environment: Environment

    def run(self, argv: Command, extra_env: Environment, timeout_s: float, stdin: bytes = b"") -> RunResult:
        """Run `argv` with the isolated environment plus `extra_env`."""
        return run_process(argv, {**self.environment, **extra_env}, timeout_s, stdin)


@dataclass(frozen=True)
class ContainerLauncher:
    """Runs commands in a running container through `docker exec` (the container's own environment)."""

    docker: str
    container: str
    environment: Environment

    def run(self, argv: Command, extra_env: Environment, timeout_s: float, stdin: bytes = b"") -> RunResult:
        """Run `argv` in the container with `extra_env` set there.

        A timeout kills the local `docker exec`; the caller removes the container, which ends
        what still runs inside.
        """
        assignments = map("=".join, extra_env.items())
        variables = [f"--env={assignment}" for assignment in assignments]
        command = [self.docker, "exec", "--interactive", *variables, self.container, *argv]
        return run_process(command, self.environment, timeout_s, stdin)
