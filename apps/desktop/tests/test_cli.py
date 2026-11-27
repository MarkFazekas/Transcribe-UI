"""Flow tests of the CLI frontend: cancellation, request files, failures, the installed entry point."""

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
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import ErrorBody, ErrorResponse, GetAppInfoRequest, GetAppInfoResponse, Response
from transcribe_ui.api.errors import CancelledError
from transcribe_ui.api.info import app_version, get_app_info
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.api.routes import Route
from transcribe_ui.api.schema import response_adapter
from transcribe_ui.cli.constants import (
    EXIT_CANCELLED,
    EXIT_ERROR,
    EXIT_INVALID_REQUEST,
    EXIT_OK,
    NOT_UTF8_MESSAGE,
    UTF8_BOM,
)
from transcribe_ui.cli.main import main
from transcribe_ui.cli.run_command import BackendFactory

GET_APP_INFO = b'{"action": "get_app_info"}'
SUBPROCESS_TIMEOUT_S = 60
CRASH_DETAIL = "internal detail that must not leak"


def _interrupt_then_finish(interrupts: int, request: GetAppInfoRequest, context: RequestContext) -> Response:
    for _ in range(interrupts):
        signal.raise_signal(signal.SIGINT)
    context.check_cancelled()
    return get_app_info(request, context)


def _seeded_backend(root: Path) -> Backend:
    # Only requests without a database run here, so the state never needs closing.
    return Backend(seeded_ports(root))


def _broken_backend() -> Backend:
    raise RuntimeError(CRASH_DETAIL)


def _run(request_file: Path, make_backend: BackendFactory | None = None) -> tuple[int, Response]:
    factory = partial(_seeded_backend, request_file.parent) if make_backend is None else make_backend
    stdout = io.BytesIO()
    code = main(["run", str(request_file)], make_backend=factory, stdout=stdout, stderr=io.BytesIO())
    return code, response_adapter().validate_json(stdout.getvalue())


def _error_code(response: Response) -> str:
    assert isinstance(response, ErrorResponse)
    return response.error.code


@pytest.mark.parametrize("interrupts", [1, 2])
def test_ctrl_c_cancels_the_request(tmp_path: Path, interrupts: int) -> None:
    """The first Ctrl+C cancels at the next check, a second one at once; both answer alike with 130."""
    run = partial(_interrupt_then_finish, interrupts)
    backend = Backend(seeded_ports(tmp_path), {"get_app_info": Route(run=run, needs_database=False)})
    request_file = tmp_path / "request.json"
    request_file.write_bytes(GET_APP_INFO)
    original_handler = signal.getsignal(signal.SIGINT)
    code, response = _run(request_file, lambda: backend)
    assert code == EXIT_CANCELLED
    assert response == ErrorResponse(action="get_app_info", error=CancelledError().body())
    assert signal.getsignal(signal.SIGINT) is original_handler


def test_unreadable_request_file_is_refused(tmp_path: Path) -> None:
    """A missing request file is answered with an error response and exit code 2."""
    code, response = _run(tmp_path / "missing.json")
    assert code == EXIT_INVALID_REQUEST
    assert _error_code(response) == "request_unreadable"


def test_utf8_bom_is_accepted(tmp_path: Path) -> None:
    """A UTF-8 request file with a byte order mark (Windows editors) is read."""
    request_file = tmp_path / "request.json"
    request_file.write_bytes(UTF8_BOM + GET_APP_INFO)
    code, response = _run(request_file)
    assert code == EXIT_OK
    assert isinstance(response, GetAppInfoResponse)


def test_utf16_request_file_is_refused(tmp_path: Path) -> None:
    """A UTF-16 request file (PowerShell 5.1 redirection) is refused with a clear message."""
    request_file = tmp_path / "request.json"
    request_file.write_bytes(GET_APP_INFO.decode("utf-8").encode("utf-16"))
    code, response = _run(request_file)
    assert code == EXIT_INVALID_REQUEST
    assert isinstance(response, ErrorResponse)
    assert response.error == ErrorBody(code="invalid_request", message=NOT_UTF8_MESSAGE)


def test_failure_outside_a_request_answers_json(tmp_path: Path) -> None:
    """A failure outside `Backend.execute` (here building the backend) gives `internal_error`, exit 1."""
    request_file = tmp_path / "request.json"
    request_file.write_bytes(GET_APP_INFO)
    code, response = _run(request_file, _broken_backend)
    assert code == EXIT_ERROR
    assert _error_code(response) == "internal_error"
    assert CRASH_DETAIL not in response_adapter().dump_json(response).decode("utf-8")


def test_missing_command_is_refused() -> None:
    """A command line without a command is answered with an error response and exit code 2."""
    stdout = io.BytesIO()
    assert main([], stdout=stdout) == EXIT_INVALID_REQUEST
    assert _error_code(response_adapter().validate_json(stdout.getvalue())) == "invalid_arguments"


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
    assert not completed.stderr
    assert b"\r" not in completed.stdout
    assert completed.stdout.endswith(b"}\n")
    response = response_adapter().validate_json(completed.stdout)
    assert isinstance(response, GetAppInfoResponse)


def test_installed_entry_point_prints_the_version() -> None:
    """`transcribe-ui --version` prints the app version with an LF line end."""
    completed = _run_entry_point("--version")
    assert completed.returncode == EXIT_OK, completed.stderr
    assert completed.stdout == f"transcribe-ui {app_version()}\n".encode()
