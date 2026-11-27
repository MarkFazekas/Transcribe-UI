"""Flow tests of the CLI frontend: cancellation, unreadable files, the installed entry point."""

import io
import signal
import subprocess
import sys
import sysconfig
from functools import partial
from pathlib import Path

import pytest

from support.fakes import seeded_ports
from support.golden import SNAPSHOT_DIR
from transcribe_ui.api.backend import Backend, Route
from transcribe_ui.api.classes import ErrorResponse, GetAppInfoRequest, GetAppInfoResponse, Response
from transcribe_ui.api.info import app_version, get_app_info
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.api.schema import response_adapter
from transcribe_ui.cli.constants import EXIT_CANCELLED, EXIT_INVALID_REQUEST, EXIT_OK
from transcribe_ui.cli.main import main

GET_APP_INFO = b'{"action": "get_app_info"}'
SUBPROCESS_TIMEOUT_S = 60


def _interrupt_then_finish(interrupts: int, request: GetAppInfoRequest, context: RequestContext) -> Response:
    for _ in range(interrupts):
        signal.raise_signal(signal.SIGINT)
    context.check_cancelled()
    return get_app_info(request, context)


def _error_code(stdout: io.BytesIO) -> str:
    response = response_adapter().validate_json(stdout.getvalue())
    assert isinstance(response, ErrorResponse)
    return response.error.code


@pytest.mark.parametrize("interrupts", [1, 2])
def test_ctrl_c_cancels_the_request(tmp_path: Path, interrupts: int) -> None:
    """The first Ctrl+C cancels at the next check, a second one at once; both exit with 130."""
    run = partial(_interrupt_then_finish, interrupts)
    backend = Backend(seeded_ports(), {"get_app_info": Route(run=run, needs_database=False)})
    request_file = tmp_path / "request.json"
    request_file.write_bytes(GET_APP_INFO)
    original_handler = signal.getsignal(signal.SIGINT)
    stdout = io.BytesIO()
    code = main(["run", str(request_file)], backend=backend, stdout=stdout, stderr=io.BytesIO())
    assert code == EXIT_CANCELLED
    assert _error_code(stdout) == "cancelled"
    assert signal.getsignal(signal.SIGINT) is original_handler


def test_unreadable_request_file_is_refused(tmp_path: Path) -> None:
    """A missing request file is answered with an error response and exit code 2."""
    stdout = io.BytesIO()
    code = main(["run", str(tmp_path / "missing.json")], stdout=stdout, stderr=io.BytesIO())
    assert code == EXIT_INVALID_REQUEST
    assert _error_code(stdout) == "request_unreadable"


def test_missing_command_is_refused() -> None:
    """A command line without a command is answered with an error response and exit code 2."""
    stdout = io.BytesIO()
    assert main([], stdout=stdout) == EXIT_INVALID_REQUEST
    assert _error_code(stdout) == "invalid_arguments"


def test_cli_schema_prints_the_request_union() -> None:
    """`transcribe-ui schema` prints the request union's schema (the committed snapshot)."""
    stdout = io.BytesIO()
    assert main(["schema"], stdout=stdout) == EXIT_OK
    assert stdout.getvalue() == (SNAPSHOT_DIR / "Request.schema.json").read_bytes()


def _run_entry_point(*args: str | Path) -> subprocess.CompletedProcess[bytes]:
    name = "transcribe-ui.exe" if sys.platform == "win32" else "transcribe-ui"
    script = Path(sysconfig.get_path("scripts")) / name
    return subprocess.run(  # noqa: S603 - runs the entry point installed from this repo
        [script, *args],
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )


def test_installed_entry_point_runs_a_request(tmp_path: Path) -> None:
    """The installed `transcribe-ui` script runs a request and prints UTF-8 JSON with LF line ends."""
    request_file = tmp_path / "kérés.json"
    request_file.write_bytes(GET_APP_INFO)
    completed = _run_entry_point("run", request_file)
    assert completed.returncode == EXIT_OK, completed.stderr
    assert b"\r" not in completed.stdout
    assert completed.stdout.endswith(b"}\n")
    response = response_adapter().validate_json(completed.stdout)
    assert isinstance(response, GetAppInfoResponse)


def test_installed_entry_point_prints_the_version() -> None:
    """`transcribe-ui --version` prints the app version with an LF line end."""
    completed = _run_entry_point("--version")
    assert completed.returncode == EXIT_OK, completed.stderr
    assert completed.stdout == f"transcribe-ui {app_version()}\n".encode()
