"""The pure parts of the desktop smoke test: bundle rules, the isolated environment, the process kill."""

import os
import sys
import time
from pathlib import Path

import pytest
from smoke_desktop_bundle import REQUIRED_DATA, cli_problems, gui_problems, platform_plugins
from smoke_desktop_process import isolated_environment, path_without_tools, run_process

# The revisions of the fake listings.
REVISIONS = ("__init__.py", "initial_schema.py")
# What every executable bundles in the fake listings.
COMMON = frozenset((*REQUIRED_DATA, *(f"transcribe_ui/state/migrations/versions/{name}" for name in REVISIONS)))
# What a Linux GUI bundles on top.
LINUX_GUI = frozenset(
    (
        "PySide6/Qt/translations/qtbase_hu.qm",
        "PySide6/Qt/plugins/platforms/libqoffscreen.so",
        "PySide6/Qt/plugins/platforms/libqxcb.so",
        "PySide6/Qt/plugins/platforms/libqwayland.so",
    ),
)
# File mode of an executable.
EXECUTABLE_MODE = 0o755
# Seconds the kill test allows in all (its timeout is one second).
KILL_LIMIT_S = 60
# A parent that starts a sleeping child, prints its process id and sleeps too.
PARENT_SCRIPT = (
    "import subprocess, sys, time; "
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)']); "
    "print(child.pid, flush=True); time.sleep(600)"
)
# Seconds between two looks at whether a killed process is gone.
POLL_S = 0.1
# A command that would run far longer than the timeout of the kill test.
SLEEPER = (sys.executable, "-c", "import time; time.sleep(600)")


def test_a_complete_cli_has_no_problems() -> None:
    """Catalogs, Alembic environment, every revision and the stamp, and no Qt."""
    assert not cli_problems(COMMON | {"transcribe_ui.cli.main", "libssl.so.3"}, REVISIONS, "linux")


def test_cli_problems_name_missing_and_forbidden() -> None:
    """A lost revision, a catalog source, Qt in any form and the host's C++ runtime are reported."""
    entries = (COMMON - {"transcribe_ui/state/migrations/versions/initial_schema.py"}) | {
        "transcribe_ui/locale/hu/LC_MESSAGES/transcribe_ui.po",
        "PySide6.QtCore",
        "libQt6Core.so.6",
        "libshiboken6.abi3.so.6.12",
        "transcribe_ui.gui.app",
        "libstdc++.so.6",
    }
    assert cli_problems(frozenset(entries), REVISIONS, "linux") == [
        "missing transcribe_ui/state/migrations/versions/initial_schema.py",
        "bundles the catalog source transcribe_ui/locale/hu/LC_MESSAGES/transcribe_ui.po",
        "bundles the host library libstdc++.so.6",
        "bundles PySide6.QtCore",
        "bundles libQt6Core.so.6",
        "bundles libshiboken6.abi3.so.6.12",
        "bundles transcribe_ui.gui.app",
    ]


def test_gui_needs_qt_texts_and_platform_plugins() -> None:
    """The Linux GUI passes with Qt's Hungarian texts and the offscreen, xcb and Wayland plugins."""
    assert not gui_problems(COMMON | LINUX_GUI, REVISIONS, "linux")
    lacking = COMMON | {"PySide6/Qt/plugins/platforms/libqoffscreen.so"}
    assert gui_problems(lacking, REVISIONS, "linux") == [
        "missing qtbase_hu.qm",
        "missing the platform plugin qxcb",
        "missing the platform plugin qwayland",
    ]


def test_platform_plugin_names_on_each_os() -> None:
    """Plugin files are named without the `lib` prefix and the suffix."""
    entries = ("PySide6/plugins/platforms/qwindows.dll", "PySide6/Qt/plugins/platforms/libqcocoa.dylib", "lib/x.so")
    assert platform_plugins(entries) == {"qwindows", "qcocoa"}


def test_path_without_tools_drops_their_folders(tmp_path: Path) -> None:
    """A folder holding ffmpeg or ffprobe leaves PATH; the others stay in order."""
    tools, plain = tmp_path / "tools", tmp_path / "plain"
    for folder in (tools, plain):
        folder.mkdir()
    suffix = ".exe" if sys.platform == "win32" else ""
    probe = tools / f"ffprobe{suffix}"
    probe.write_bytes(b"")
    probe.chmod(EXECUTABLE_MODE)
    assert path_without_tools(os.pathsep.join((str(tools), str(plain)))) == str(plain)


def test_isolated_environment_uses_fresh_state(tmp_path: Path) -> None:
    """The app's own variables are dropped; platformdirs is pointed at the fresh state."""
    base = {"PATH": "", "QT_QPA_PLATFORM": "xcb", "R2_BUCKET": "real", "KEEP": "1"}
    environment = isolated_environment(base, tmp_path)
    assert "QT_QPA_PLATFORM" not in environment
    assert "R2_BUCKET" not in environment
    assert environment["KEEP"] == "1"
    state = "WIN_PD_OVERRIDE_LOCAL_APPDATA" if sys.platform == "win32" else "XDG_STATE_HOME"
    assert Path(environment[state]).is_relative_to(tmp_path)


def test_a_process_past_its_timeout_is_killed() -> None:
    """The run ends after its timeout with no exit code, instead of waiting for the process."""
    run = run_process(SLEEPER, os.environ, 1)
    assert run.returncode is None
    assert run.seconds < KILL_LIMIT_S


def test_output_and_input_pass_through_files() -> None:
    """The input is fed from bytes and the output is read back unchanged."""
    echo = (sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())")
    run = run_process(echo, os.environ, KILL_LIMIT_S, b"ok")
    assert (run.returncode, run.stdout) == (0, b"OK")


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX session kill; Windows kills the tree with taskkill")
def test_a_timeout_kills_the_grandchildren_too() -> None:
    """A process that started a child of its own: both are gone after the timeout (one session)."""
    parent = (sys.executable, "-c", PARENT_SCRIPT)
    run = run_process(parent, os.environ, 2)
    grandchild = int(run.stdout.split()[0])
    deadline = time.monotonic() + KILL_LIMIT_S
    while not _gone(grandchild) and time.monotonic() < deadline:
        time.sleep(POLL_S)
    assert run.returncode is None
    assert _gone(grandchild)
