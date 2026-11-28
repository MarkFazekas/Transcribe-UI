"""The CLI, GUI and bundle checks of the desktop smoke test (docs/ai/plans/m8.md §1 "Smoke tests").

Used by `scripts/smoke_desktop_build.py` (on the runner) and `scripts/smoke_desktop_floor.py`
(the same checks inside an ubuntu:22.04 container). Every check returns an `Outcome`; none
raises for a failing executable.
"""

import gettext
import json
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from smoke_desktop_bundle import Revisions, list_bundle
from smoke_desktop_process import Command, Launcher, RunResult

# A JSON object (a request or a response of `run -`).
type JsonObject = Mapping[str, object]
# A response of `run -`, or None when stdout held no JSON object.
type Answer = tuple[RunResult, JsonObject | None]
# What the archive of an executable lacks or must not have, from its entries, the revisions and `sys.platform`.
type BundleRules = Callable[[frozenset[str], Revisions, str], list[str]]

# Root of the repository.
REPO_ROOT = Path(__file__).resolve().parent.parent
# The app's package sources.
PACKAGE_DIR = REPO_ROOT / "apps/desktop/src/transcribe_ui"
# The repository's Hungarian catalog, read for the expected help text.
HUNGARIAN_CATALOG = PACKAGE_DIR / "locale" / "hu" / "LC_MESSAGES" / "transcribe_ui.mo"
# The repository's Alembic revisions, each of which every executable must bundle.
REVISIONS_DIR = PACKAGE_DIR / "state" / "migrations" / "versions"
# English help text of `--version`, whose Hungarian text `cli-help-hu` looks for.
VERSION_HELP = "print the app version and exit"
# Environment of a GUI run in the smoke mode.
SMOKE_MODE: Mapping[str, str] = MappingProxyType({"TRANSCRIBE_UI_SMOKE_TEST": "1"})
# Status of a response that succeeded.
STATUS_OK = "ok"


@dataclass(frozen=True)
class Outcome:
    """The result of one check: whether it passed, what went wrong and how long it took."""

    name: str
    passed: bool
    detail: str
    seconds: float


@dataclass(frozen=True)
class Smoke:
    """What every check needs: the executables, the expected version and where and how long they run."""

    cli: str
    gui: str
    version: str
    launcher: Launcher
    cli_timeout_s: float
    gui_timeout_s: float
    # Environment that makes a run Hungarian on Linux (the language setting does it elsewhere).
    hungarian_env: Mapping[str, str]


def failed(name: str, detail: str, seconds: float = 0) -> Outcome:
    """Return a failed outcome."""
    return Outcome(name, passed=False, detail=detail, seconds=seconds)


def judged(name: str, run: RunResult, problem: str | None) -> Outcome:
    """Return the outcome of a check of one run: a timeout or a problem fails it."""
    if run.returncode is None:
        reason = f"timed out after {run.seconds:.0f} s"
        return failed(name, f"{reason}\n{run.text()}", run.seconds)
    if problem is not None:
        reason = f"{problem} (exit {run.returncode})"
        return failed(name, f"{reason}\n{run.text()}", run.seconds)
    return Outcome(name, passed=True, detail="", seconds=run.seconds)


def _respond(smoke: Smoke, request: JsonObject) -> Answer:
    """Run one request with `<cli> run -`; return the run and its JSON response."""
    document = json.dumps(request).encode()
    argv = [smoke.cli, "run", "-"]
    run = smoke.launcher.run(argv, {}, smoke.cli_timeout_s, document)
    try:
        response = json.loads(run.stdout)
    except ValueError:
        return run, None
    return run, response if isinstance(response, dict) else None


def _answered_ok(answer: Answer) -> bool:
    run, response = answer
    if run.returncode != 0 or response is None:
        return False
    return response.get("status") == STATUS_OK


def check_cli_version(smoke: Smoke) -> Outcome:
    """`<cli> --version` exits 0 and prints exactly `transcribe-ui <expected>`."""
    run = smoke.launcher.run([smoke.cli, "--version"], {}, smoke.cli_timeout_s)
    expected = f"transcribe-ui {smoke.version}\n".encode()
    matches = run.returncode == 0 and run.stdout == expected
    return judged("cli-version", run, None if matches else f"expected {expected!r}")


def check_cli_database(smoke: Smoke) -> Outcome:
    """`list_batches` answers `ok`: the fresh database was created and migrated by the bundled Alembic environment."""
    answer = _respond(smoke, {"action": "list_batches"})
    return judged("cli-database", answer[0], None if _answered_ok(answer) else "list_batches did not answer ok")


def _use_hungarian(smoke: Smoke) -> str | None:
    """Set the `language` setting to `hu` through `get_settings` and `update_settings`; return the problem."""
    current = _respond(smoke, {"action": "get_settings"})
    response = current[1] or {}
    settings = response.get("settings")
    if not _answered_ok(current) or not isinstance(settings, dict):
        return f"get_settings did not answer ok\n{current[0].text()}"
    updated = _respond(smoke, {"action": "update_settings", "settings": {**settings, "language": "hu"}})
    if not _answered_ok(updated):
        return f"update_settings did not answer ok\n{updated[0].text()}"
    return None


def _hungarian_version_help() -> str:
    with HUNGARIAN_CATALOG.open("rb") as stream:
        return gettext.GNUTranslations(stream).gettext(VERSION_HELP)


def _words(text: str) -> str:
    """Return the text with every run of whitespace as one space (argparse wraps help texts)."""
    return " ".join(text.split())


def check_cli_help_hu(smoke: Smoke) -> Outcome:
    """`--help` in Hungarian shows the Hungarian help of `--version` from the repository's catalog.

    On Linux the environment's `LANGUAGE` makes it Hungarian; Windows and macOS do not read the
    OS language from the environment, so there the `language` setting is set to `hu` first (and
    the GUI checks after it run in Hungarian too).
    """
    expected = _hungarian_version_help()
    if expected == VERSION_HELP:
        return failed("cli-help-hu", f"{HUNGARIAN_CATALOG} has no Hungarian text")
    if sys.platform != "linux":
        problem = _use_hungarian(smoke)
        if problem is not None:
            return failed("cli-help-hu", problem)
    argv = [smoke.cli, "--help"]
    run = smoke.launcher.run(argv, smoke.hungarian_env, smoke.cli_timeout_s)
    shown = _words(run.stdout.decode("utf-8", errors="replace"))
    found = run.returncode == 0 and _words(expected) in shown
    return judged("cli-help-hu", run, None if found else f"no {expected!r} in --help")


def check_bundle(name: str, executable: str, rules: BundleRules) -> Outcome:
    """List the executable's archive (or its macOS app's files) and check what it must and must not contain."""
    started = time.monotonic()
    listing = list_bundle(executable)
    seconds = time.monotonic() - started
    if listing.error is not None:
        return failed(name, listing.error, seconds)
    revisions = sorted(path.name for path in REVISIONS_DIR.glob("*.py"))
    problems = rules(listing.entries, revisions, sys.platform)
    detail = "\n".join(problems)
    return Outcome(name, passed=not problems, detail=detail, seconds=seconds)


def check_gui(name: str, smoke: Smoke, command: Command, platform: str) -> Outcome:
    """The GUI in its smoke mode on the Qt platform `platform` starts, answers its start-up and exits 0 by itself."""
    environment = {**SMOKE_MODE, **smoke.hungarian_env, "QT_QPA_PLATFORM": platform}
    run = smoke.launcher.run(command, environment, smoke.gui_timeout_s)
    return judged(name, run, None if run.returncode == 0 else "the smoke mode of the GUI failed")
