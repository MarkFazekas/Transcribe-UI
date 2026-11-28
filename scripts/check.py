"""Run lint, typecheck and tests for every workspace member in parallel.

Usage (from anywhere in the repo): `uv run scripts/check.py [--fix] [target ...]`

Targets (the repo tooling and each workspace member) run at the same time; the steps of one
target run one after the other, and each target's output is printed as one block when it
finishes. `--fix` formats and applies safe ruff fixes first. Exit code 1 if any step failed.
"""

import argparse
import io
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MEMBER_PARENTS = ("packages", "apps")
TOOLING_PATHS = (".claude/hooks", "scripts")
# Tests of the repo tooling (the build and smoke scripts' pure parts).
TOOLING_TESTS = "scripts/tests"
# Folder of a member with files that are no package but are typed too (the desktop's PyInstaller hooks).
EXTRA_TYPED_FOLDER = "packaging"
# Targets whose tests use Qt (pytest-qt is disabled for the others).
QT_TARGETS = frozenset(("desktop",))
# Targets with translation catalogs, checked by `scripts/catalogs.py check`.
CATALOG_TARGETS = frozenset(("desktop",))
# The catalog check (current files, complete Hungarian texts).
CATALOG_CHECK = str(REPO_ROOT / "scripts" / "catalogs.py")


@dataclass(frozen=True)
class Target:
    """A group of paths checked together."""

    name: str
    lint_paths: tuple[str, ...]
    type_paths: tuple[str, ...]
    test_path: str | None


@dataclass(frozen=True)
class Step:
    """One tool run of a target."""

    title: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class StepOutcome:
    """Outcome of one step."""

    title: str
    returncode: int
    output: str


@dataclass(frozen=True)
class TargetOutcome:
    """Outcome of every step of one target."""

    target: Target
    steps: tuple[StepOutcome, ...]
    elapsed_s: float


def discover_targets() -> list[Target]:
    """Return the repo tooling plus every workspace member."""
    member_parents = [REPO_ROOT / parent for parent in MEMBER_PARENTS]
    manifests = sorted(path for folder in member_parents for path in folder.glob("*/pyproject.toml"))
    members = [_member_target(manifest.parent) for manifest in manifests]
    return [Target("tooling", TOOLING_PATHS, TOOLING_PATHS, TOOLING_TESTS), *members]


def _member_target(member_dir: Path) -> Target:
    relative = member_dir.relative_to(REPO_ROOT).as_posix()
    tests = f"{relative}/tests"
    typed = [f"{relative}/src"]
    if (member_dir / EXTRA_TYPED_FOLDER).is_dir():
        typed.append(f"{relative}/{EXTRA_TYPED_FOLDER}")
    if (REPO_ROOT / tests).is_dir():
        return Target(member_dir.name, (relative,), (*typed, tests), tests)
    return Target(member_dir.name, (relative,), tuple(typed), None)


def _tool(title: str, module: str, *args: str) -> Step:
    return Step(title, (sys.executable, "-m", module, *args))


def build_steps(target: Target, *, fix: bool) -> list[Step]:
    """Return the steps of one target, in order."""
    lint = target.lint_paths
    steps = []
    if fix:
        steps.append(_tool("ruff format", "ruff", "format", *lint))
        steps.append(_tool("ruff fix", "ruff", "check", "--fix", *lint))
    # Separate mypy cache per target: the targets run in parallel.
    cache = f".mypy_cache/{target.name}"
    steps.extend(
        (
            _tool("ruff format --check", "ruff", "format", "--check", *lint),
            _tool("ruff check", "ruff", "check", *lint),
            _tool("flake8", "flake8", *lint),
            _tool("mypy", "mypy", "--cache-dir", cache, *target.type_paths),
        ),
    )
    if target.name in CATALOG_TARGETS:
        steps.append(Step("catalogs", (sys.executable, CATALOG_CHECK, "check")))
    if target.test_path is not None:
        # pytest-qt imports QtGui/QtWidgets when pytest starts; only the GUI's target has the
        # system libraries Qt loads (CI installs them for the desktop leg only).
        plugins = () if target.name in QT_TARGETS else ("-p", "no:pytest-qt")
        steps.append(_tool("pytest", "pytest", "-q", *plugins, target.test_path))
    return steps


def run_target(target: Target, *, fix: bool) -> TargetOutcome:
    """Run every step of a target, in order, and collect the outcomes."""
    started = time.monotonic()
    steps = build_steps(target, fix=fix)
    outcomes = tuple(_run_step(step) for step in steps)
    return TargetOutcome(target, outcomes, time.monotonic() - started)


def _run_step(step: Step) -> StepOutcome:
    try:
        completed = subprocess.run(
            step.argv,
            cwd=REPO_ROOT,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as error:
        message = f"could not run {step.title}: {error}"
        return StepOutcome(step.title, -1, message)
    return StepOutcome(step.title, completed.returncode, f"{completed.stdout}{completed.stderr}")


def report(outcome: TargetOutcome) -> bool:
    """Print one target's block; return True if every step passed."""
    passed = all(step.returncode == 0 for step in outcome.steps)
    status = "ok" if passed else "FAILED"
    name = outcome.target.name
    sys.stdout.write(f"=== {name}: {status} ({outcome.elapsed_s:.1f} s)\n")
    for step in outcome.steps:
        mark = "ok" if step.returncode == 0 else f"exit {step.returncode}"
        sys.stdout.write(f"  - {step.title}: {mark}\n")
        if step.returncode:
            sys.stdout.write(f"{step.output.rstrip()}\n")
    sys.stdout.flush()
    return passed


def _is_selected(target: Target, selected: set[str]) -> bool:
    return not selected or target.name in selected


def main() -> int:
    """Parse arguments, run the targets in parallel and report."""
    parser = argparse.ArgumentParser(description="Run lint, typecheck and tests in parallel.")
    parser.add_argument("--fix", action="store_true", help="format and apply safe ruff fixes first")
    parser.add_argument("targets", nargs="*", help="limit to these targets (default: all)")
    args = parser.parse_args()
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    discovered = discover_targets()
    selected = set(args.targets)
    unknown = selected - {target.name for target in discovered}
    if unknown:
        sys.stderr.write(f"unknown targets: {', '.join(sorted(unknown))}\n")
        return 2
    targets = [target for target in discovered if _is_selected(target, selected)]
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        futures = [pool.submit(run_target, target, fix=args.fix) for target in targets]
        passed = [report(future.result()) for future in as_completed(futures)]
    return 0 if all(passed) else 1


if __name__ == "__main__":
    sys.exit(main())
