"""Flow tests of `run -` (the request from stdin) and `run --progress text` (plan m6, M6.7)."""

import io
import json
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import BinaryIO

import pytest

from support.fakes import seeded_ports
from support.golden import assert_golden
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import GetAppInfoRequest, ProgressEvent, Response
from transcribe_ui.api.info import get_app_info
from transcribe_ui.api.ports import Ports, RequestContext
from transcribe_ui.api.routes import Route
from transcribe_ui.cli.console import Console
from transcribe_ui.cli.constants import EXIT_INVALID_REQUEST, EXIT_OK, UTF8_BOM
from transcribe_ui.cli.main import main
from transcribe_ui.cli.progress import printable
from transcribe_ui.i18n.constants import Language
from transcribe_ui.state.settings import default_settings

ACTION = "get_app_info"
GET_APP_INFO = '{"action": "get_app_info"}'
RUN = "run"
# Progress values of the scripted events.
FOLDERS_FOUND = 500
FILES = 12
SENT_BYTES = 1_048_576
FILE_BYTES = 73_400_320
# The languages of the texts.
LANGUAGES: tuple[Language, ...] = ("en", "hu")
# Request documents covering every way a request ends before or after the backend: success, an
# error response (with a non-ASCII value echoed), invalid JSON, an invalid request (a non-ASCII
# field name echoed in the problem's location).
DOCUMENTS = (
    GET_APP_INFO.encode(),
    UTF8_BOM + GET_APP_INFO.encode(),
    b'{"action": "get_batch", "batch_id": "batch_20261127_120000"}',
    b'{"action": ',
    '{"action": "get_app_info", "kérés": 1}'.encode(),
    GET_APP_INFO.encode("utf-16"),
)
# The progress events of the scripted request: with and without a total and a path, the stage
# of the uploaded bytes, a path with control characters (shown escaped), and a stage without a
# catalog entry (shown as its code).
EVENTS = (
    ProgressEvent(action=ACTION, stage="scan", completed=FOLDERS_FOUND, path="/media/Filmek/Évad 1"),
    ProgressEvent(action=ACTION, stage="probe", completed=3, total=FILES, path="/media/Filmek/Évad 1/rész.mkv"),
    ProgressEvent(action=ACTION, stage="upload_bytes", completed=SENT_BYTES, total=FILE_BYTES, path="/media/a.mkv"),
    ProgressEvent(action=ACTION, stage="probe", completed=4, total=FILES, path="/media/new\nline\r\x1b[2J.mkv"),
    ProgressEvent(action=ACTION, stage="download", completed=FILES, total=FILES),
    ProgressEvent(action=ACTION, stage="future_stage", completed=1),
)


def _report_progress(request: GetAppInfoRequest, context: RequestContext) -> Response:
    for event in EVENTS:
        context.progress.emit(event)
    return get_app_info(request, context)


@pytest.fixture
def ports(tmp_path: Path) -> Iterator[Ports]:
    """Return seeded ports (closed afterwards)."""
    seeded = seeded_ports(tmp_path / "state")
    with closing(seeded.state):
        yield seeded


@contextmanager
def _ports_in(root: Path, language: Language) -> Iterator[Ports]:
    """Return seeded ports whose settings choose this language (closed afterwards)."""
    seeded = seeded_ports(root)
    seeded.state.settings.save(default_settings().model_copy(update={"language": language}))
    with closing(seeded.state):
        yield seeded


def _cli(argv: list[str], backend: Backend, stdin: BinaryIO | None = None) -> tuple[int, bytes, bytes]:
    stdout = io.BytesIO()
    stderr = io.BytesIO()
    console = Console(stdin=io.BytesIO() if stdin is None else stdin, stdout=stdout, stderr=stderr)
    code = main(argv, make_backend=lambda: backend, console=console)
    return code, stdout.getvalue(), stderr.getvalue()


@pytest.mark.parametrize("document", DOCUMENTS)
def test_stdin_and_file_give_the_same_answer(tmp_path: Path, ports: Ports, document: bytes) -> None:
    """`run -` with the document on stdin answers exactly like `run <file>` with it in a file."""
    request_file = tmp_path / "request.json"
    request_file.write_bytes(document)
    backend = Backend(ports)
    from_file = _cli([RUN, str(request_file)], backend)
    from_stdin = _cli([RUN, "-"], backend, io.BytesIO(document))
    assert from_stdin == from_file


class UnreadableStream(io.BytesIO):
    """A stdin whose read fails (a closed console)."""

    def read(self, _size: int | None = -1, /) -> bytes:
        """Fail.

        Raises:
            OSError: Always.
        """
        raise OSError(5, "Input/output error")


def test_unreadable_stdin_is_refused(ports: Ports) -> None:
    """A stdin that cannot be read is answered with `request_unreadable` and exit code 2."""
    code, stdout, stderr = _cli([RUN, "-"], Backend(ports), UnreadableStream())
    assert code == EXIT_INVALID_REQUEST
    assert json.loads(stdout)["error"]["code"] == "request_unreadable"
    assert not stderr


@pytest.mark.parametrize("language", LANGUAGES)
def test_text_progress(tmp_path: Path, language: Language) -> None:
    """`--progress text` writes one readable line per event in the setting's language; stdout stays JSON."""
    request_file = tmp_path / "request.json"
    request_file.write_text(GET_APP_INFO, encoding="utf-8")
    with _ports_in(tmp_path / "state", language) as ports:
        backend = Backend(ports, {ACTION: Route(run=_report_progress, needs_database=False)})
        code, stdout, stderr = _cli([RUN, "--progress", "text", str(request_file)], backend)
        assert stdout == _cli([RUN, str(request_file)], backend)[1]
    assert code == EXIT_OK
    assert len(stderr.splitlines()) == len(EVENTS)
    assert_golden(f"cli_progress_text_{language}.txt", stderr.decode("utf-8"))


def test_text_progress_escapes_what_is_not_printable() -> None:
    """Control characters and undecodable bytes (surrogates) of a path are shown escaped, the rest as is."""
    assert printable("/a\tb\x1b[0m\udcffé ő") == r"/a\tb\x1b[0m\udcffé ő"


def test_ndjson_progress_is_the_default(tmp_path: Path, ports: Ports) -> None:
    """Without `--progress` (and with `--progress ndjson`) each event is one JSON line, as before."""
    request_file = tmp_path / "request.json"
    request_file.write_text(GET_APP_INFO, encoding="utf-8")
    backend = Backend(ports, {ACTION: Route(run=_report_progress, needs_database=False)})
    default = _cli([RUN, str(request_file)], backend)
    assert _cli([RUN, "--progress", "ndjson", str(request_file)], backend) == default
    events = [ProgressEvent.model_validate_json(line) for line in default[2].splitlines()]
    assert events == list(EVENTS)
