"""The `linux-floor` check of the desktop smoke test (docs/ai/plans/m8.md §1 "Smoke tests").

In an ubuntu:22.04 container (the oldest Ubuntu LTS the build supports) with only the libraries
of `apps/desktop/packaging/linux-packages.txt` installed, the CLI's version and database checks
and the GUI's offscreen start pass. The executables are mounted read-only; the container is
removed afterwards whatever happened, which also ends a run that timed out inside it.
"""

import os
import shutil
import time
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from smoke_desktop_checks import Outcome, Smoke, check_cli_database, check_cli_version, check_gui, failed, judged
from smoke_desktop_process import ContainerLauncher, HostLauncher, RunResult

# System libraries the Linux executables need (one Ubuntu package per line, `#` comments).
LINUX_PACKAGES_FILE = Path(__file__).resolve().parent.parent / "apps/desktop/packaging/linux-packages.txt"
# Name of the check.
FLOOR_CHECK = "linux-floor"
# Where the container sees the CLI executable.
FLOOR_CLI = "/smoke/transcribe-ui-cli"
# Where the container sees the GUI executable.
FLOOR_GUI = "/smoke/transcribe-ui-gui"
# Seconds starting the container may take (the image is pulled first).
START_TIMEOUT_S = 600
# Seconds installing the libraries may take.
SETUP_TIMEOUT_S = 900
# Seconds removing the container or reading its logs may take.
CLEANUP_TIMEOUT_S = 120
# Shell command that installs the packages named after it, quietly and without recommended ones.
INSTALL = "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends"
# Starts a container in the background, removed when it stops; its name follows.
DETACHED = ("run", "--detach", "--rm", "--name")
# Command that keeps the container running until it is removed.
KEEP_RUNNING = ("sleep", "infinity")
# Shell command that prints every log file of the app in the container.
SHOW_LOGS = 'for log in $(find / -xdev -name "transcribe-ui-*.log"); do echo "== $log"; cat "$log"; done'


def linux_packages() -> list[str]:
    """Return the Ubuntu packages of the libraries the Linux executables need."""
    text = LINUX_PACKAGES_FILE.read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


@dataclass(frozen=True)
class Docker:
    """The Docker CLI, run with the runner's own environment (not the app's isolated one)."""

    executable: str
    launcher: HostLauncher

    def run(self, arguments: Sequence[str], timeout_s: float) -> RunResult:
        """Run `docker <arguments>`."""
        return self.launcher.run([self.executable, *arguments], {}, timeout_s)


@dataclass(frozen=True)
class Container:
    """A running container of the floor image."""

    docker: Docker
    name: str

    def shell(self, script: str, timeout_s: float) -> RunResult:
        """Run a shell script in the container."""
        return self.docker.run(["exec", self.name, "sh", "-c", script], timeout_s)

    def checks(self, smoke: Smoke) -> list[Outcome]:
        """Run the CLI's version and database checks and the GUI's offscreen start in the container."""
        inside = ContainerLauncher(self.docker.executable, self.name, self.docker.launcher.environment)
        hungarian = {"LANGUAGE": "hu"}
        timeouts = (smoke.cli_timeout_s, smoke.gui_timeout_s)
        in_floor = Smoke(FLOOR_CLI, FLOOR_GUI, smoke.version, inside, *timeouts, hungarian_env=hungarian)
        gui = check_gui("gui-offscreen", in_floor, [FLOOR_GUI], "offscreen")
        return [check_cli_version(in_floor), check_cli_database(in_floor), gui]


def _start(container: Container, smoke: Smoke, image: str) -> RunResult:
    mounts = [_mount(smoke.cli, FLOOR_CLI), _mount(smoke.gui, FLOOR_GUI)]
    command = [*DETACHED, container.name, *mounts, image, *KEEP_RUNNING]
    return container.docker.run(command, START_TIMEOUT_S)


def _mount(source: str, target: str) -> str:
    return f"--volume={source}:{target}:ro"


def _inside(container: Container, smoke: Smoke) -> Outcome:
    started = time.monotonic()
    packages = " ".join(linux_packages())
    setup = container.shell(f"{INSTALL} {packages}", SETUP_TIMEOUT_S)
    if setup.returncode != 0:
        return judged(FLOOR_CHECK, setup, "installing the libraries failed")
    problems = [outcome for outcome in container.checks(smoke) if not outcome.passed]
    if not problems:
        seconds = time.monotonic() - started
        return Outcome(FLOOR_CHECK, passed=True, detail="", seconds=seconds)
    details = [f"{outcome.name}: {outcome.detail}" for outcome in problems]
    logs = container.shell(SHOW_LOGS, CLEANUP_TIMEOUT_S).text()
    seconds = time.monotonic() - started
    return failed(FLOOR_CHECK, "\n".join([*details, logs]), seconds)


def check_linux_floor(smoke: Smoke, image: str | None) -> Outcome:
    """In an ubuntu:22.04 container with only the listed libraries, the CLI and the offscreen GUI work."""
    executable = shutil.which("docker")
    if image is None or executable is None:
        return failed(FLOOR_CHECK, "needs --floor-image and docker on PATH")
    docker = Docker(executable, HostLauncher(dict(os.environ)))
    container = Container(docker, f"transcribe-ui-floor-{os.getpid()}")
    with ExitStack() as cleanup:
        # Registered first: also removes a container whose start timed out (a slow pull), and
        # ends whatever a timed-out `docker exec` left running inside.
        cleanup.callback(docker.run, ["rm", "--force", container.name], CLEANUP_TIMEOUT_S)
        start = _start(container, smoke, image)
        if start.returncode != 0:
            return judged(FLOOR_CHECK, start, "the container did not start")
        return _inside(container, smoke)
