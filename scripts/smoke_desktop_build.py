"""Smoke-test the two desktop executables of a build leg (docs/ai/plans/m8.md §1 "Smoke tests").

Usage, from the repository root, in the build environment (PyInstaller lists the archives):

    uv run --no-sync python scripts/smoke_desktop_build.py <cli> <gui> --version <expected>
        [--floor-image <ubuntu:22.04 image>] [--skip <check> ...]

The checks run in order on fresh per-user state (a temporary folder for the app's data,
settings, cache and logs, so none of the machine's own is read or changed) and with `PATH`
stripped of every folder holding ffmpeg or ffprobe. The exception is the OS keyring:
`credentials_status` reads the real Windows Credential Manager or macOS Keychain (read only;
nothing is stored), which has no override. `cli-probe` gets only the OS's own folders on `PATH`
(without one holding ffmpeg or ffprobe), so it passes only with the bundled ffprobe. Each check
prints one line; a summary (sizes, the CLI's cold start) follows and is appended to the job
summary (`GITHUB_STEP_SUMMARY`). A failing check prints the process output and the app's log
files. Every process has a timeout, after which it is killed with everything it started.
`linux-floor` runs only on Linux and needs `--floor-image`; `--skip` leaves a check out (for a
local run without Docker or Xvfb; CI skips nothing). Exit code 1 if any check failed.
"""

import argparse
import os
import platform
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from pathlib import Path
from types import MappingProxyType

from smoke_desktop_bundle import cli_problems, gui_problems
from smoke_desktop_checks import (
    Outcome,
    Smoke,
    check_bundle,
    check_cli_database,
    check_cli_help_hu,
    check_cli_version,
    check_gui,
    failed,
)
from smoke_desktop_floor import FLOOR_CHECK, check_linux_floor
from smoke_desktop_probe import PROBE_CHECK, ProbeSetup, check_cli_probe, minimal_path
from smoke_desktop_process import HostLauncher, isolated_environment

# A check, ready to run.
type Check = Callable[[], Outcome]
# The checks of a run, by name, in the order they run.
type Plan = list[tuple[str, Check]]

# The checks in the order they run.
CHECKS = (
    "cli-version",
    "cli-database",
    PROBE_CHECK,
    "cli-help-hu",
    "cli-bundle",
    "gui-bundle",
    "gui-offscreen",
    "gui-native",
    FLOOR_CHECK,
)
# Factor of every timeout on Windows, where unpacking a one-file executable is slow.
WINDOWS_SLOWDOWN = 3
# Seconds a CLI run may take (before the Windows factor).
CLI_TIMEOUT_S = 90
# Seconds a GUI run may take (before the Windows factor); longer than the smoke mode's own deadline.
GUI_TIMEOUT_S = 240
# The native platform plugin of Qt, by `sys.platform`.
NATIVE_PLATFORMS: Mapping[str, str] = MappingProxyType({"win32": "windows", "darwin": "cocoa", "linux": "xcb"})
# How `gui-native` starts the GUI on Linux: under a virtual X server.
VIRTUAL_DISPLAY = ("xvfb-run", "--auto-servernum", "--server-args=-screen 0 1280x1024x24")
# Bytes in a megabyte, for the summary.
MEGABYTE = 1_000_000


def planned_checks(smoke: Smoke, probe: ProbeSetup, floor_image: str | None, skipped: Sequence[str]) -> Plan:
    """Return the checks of this platform in their order, without the skipped ones."""
    native = NATIVE_PLATFORMS[sys.platform]
    on_display = VIRTUAL_DISPLAY if sys.platform == "linux" else ()
    checks: dict[str, Check] = {
        "cli-version": partial(check_cli_version, smoke),
        "cli-database": partial(check_cli_database, smoke),
        PROBE_CHECK: partial(check_cli_probe, smoke, probe),
        "cli-help-hu": partial(check_cli_help_hu, smoke),
        "cli-bundle": partial(check_bundle, "cli-bundle", smoke.cli, cli_problems),
        "gui-bundle": partial(check_bundle, "gui-bundle", smoke.gui, gui_problems),
        "gui-offscreen": partial(check_gui, "gui-offscreen", smoke, [smoke.gui], "offscreen"),
        "gui-native": partial(check_gui, "gui-native", smoke, [*on_display, smoke.gui], native),
    }
    if sys.platform == "linux":
        checks[FLOOR_CHECK] = partial(check_linux_floor, smoke, floor_image)
    selected = [name for name in CHECKS if name in checks]
    return [(name, checks[name]) for name in selected if name not in skipped]


def run_check(name: str, check: Check) -> Outcome:
    """Run one check and print its line (and what went wrong)."""
    try:
        outcome = check()
    except OSError as error:
        # A program that cannot be started (a missing virtual display, a file that is no executable).
        outcome = failed(name, f"could not run: {error}")
    mark = "ok" if outcome.passed else "FAILED"
    sys.stdout.write(f"{mark:<6} {name} ({outcome.seconds:.1f} s)\n")
    if not outcome.passed:
        sys.stdout.write(f"{outcome.detail}\n")
    sys.stdout.flush()
    return outcome


def _row(outcome: Outcome) -> str:
    mark = "ok" if outcome.passed else "FAILED"
    return f"| {outcome.name} | {mark} | {outcome.seconds:.1f} |"


def _size(executable: str) -> str:
    path = Path(executable)
    return f"{path.name} {path.stat().st_size / MEGABYTE:.1f} MB"


def summary(smoke: Smoke, outcomes: Sequence[Outcome]) -> str:
    """Return the Markdown summary: every check, the sizes and the CLI's cold start."""
    rows = "\n".join(_row(outcome) for outcome in outcomes)
    sizes = f"{_size(smoke.cli)}, {_size(smoke.gui)}"
    cold = [f"{outcome.seconds:.1f} s" for outcome in outcomes if outcome.name == "cli-version"]
    return (
        f"### Desktop smoke test, {sys.platform} {platform.machine()}\n\n"
        f"| Check | Result | Seconds |\n|---|---|---|\n{rows}\n\n"
        f"Sizes: {sizes}. CLI cold start (`--version`): {''.join(cold) or 'not measured'}.\n"
    )


def print_logs(state_root: Path) -> None:
    """Print every log file the executables wrote under the fresh state."""
    for log in sorted(state_root.rglob("*.log")):
        text = log.read_text(encoding="utf-8", errors="replace")
        sys.stdout.write(f"== {log}\n{text}\n")


def _smoke(executables: Sequence[Path], version: str, state_root: Path) -> Smoke:
    scale = WINDOWS_SLOWDOWN if sys.platform == "win32" else 1
    hungarian = {"LANGUAGE": "hu"} if sys.platform == "linux" else {}
    launcher = HostLauncher(isolated_environment(os.environ, state_root))
    cli, gui = (str(path) for path in executables)
    timeouts = (CLI_TIMEOUT_S * scale, GUI_TIMEOUT_S * scale)
    return Smoke(cli, gui, version, launcher, *timeouts, hungarian_env=hungarian)


def run_all(executables: Sequence[Path], args: argparse.Namespace) -> list[Outcome]:
    """Run every planned check on fresh per-user state; print the logs when one failed."""
    with tempfile.TemporaryDirectory(prefix="transcribe-ui-smoke-", ignore_cleanup_errors=True) as folder:
        state_root = Path(folder)
        smoke = _smoke(executables, args.version, state_root)
        media = state_root / "probe-media"
        media.mkdir()
        probe = ProbeSetup(media, str(media), minimal_path())
        plan = planned_checks(smoke, probe, args.floor_image, args.skip)
        outcomes = [run_check(*planned) for planned in plan]
        if not all(outcome.passed for outcome in outcomes):
            print_logs(state_root)
    report = summary(smoke, outcomes)
    sys.stdout.write(report)
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with Path(summary_file).open("a", encoding="utf-8") as stream:
            stream.write(report)
    return outcomes


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(description="Smoke-test the desktop executables of a build leg.")
    parser.add_argument("cli", type=Path, help="the CLI executable")
    parser.add_argument("gui", type=Path, help="the GUI executable")
    parser.add_argument("--version", required=True, help="the version the executables must report")
    parser.add_argument("--floor-image", help="the ubuntu:22.04 image of linux-floor (Linux)")
    parser.add_argument("--skip", action="append", default=[], choices=CHECKS, help="leave a check out (local runs)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the checks; return 0 when every one passed, 1 when one failed, 2 for a missing executable."""
    args = parse_arguments(argv)
    executables = [args.cli.resolve(), args.gui.resolve()]
    missing = [path for path in executables if not path.is_file()]
    if missing:
        names = ", ".join(map(str, missing))
        sys.stderr.write(f"not found: {names}\n")
        return 2
    outcomes = run_all(executables, args)
    return 0 if all(outcome.passed for outcome in outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
