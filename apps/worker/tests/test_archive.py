"""Regression tests of the pinned-archive download and unpacking used by the image build."""

import hashlib
import io
import tarfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import override

import pytest

from transcribe_worker.engine.archive import ArchiveError, fetch, unpack_single_folder

TOP = "silero-vad-0123"
ENTRY_FILE = "hubconf.py"
BODY = b"dependencies = []\n"
SERVICE_UNAVAILABLE = 503
NOT_FOUND = 404
OK = 200
WRONG_SHA256 = "0" * 64  # noqa: WPS432 - length of a hex SHA-256


def make_archive(members: dict[str, bytes], link: str | None = None) -> bytes:
    """Return a .tar.gz holding the given files, plus a symlink to `link` when given."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, body in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(body)
            tar.addfile(member, io.BytesIO(body))
        if link is not None:
            symlink = tarfile.TarInfo(f"{TOP}/escape")
            symlink.type = tarfile.SYMTYPE
            symlink.linkname = link
            tar.addfile(symlink)
    return buffer.getvalue()


class ArchiveServer(ThreadingHTTPServer):
    """Serves one archive after answering `failures` requests with `failure_status`."""

    def __init__(self, archive: bytes, failures: int, failure_status: int) -> None:
        """Listen on a free localhost port."""
        super().__init__(("127.0.0.1", 0), ArchiveHandler)
        self.archive = archive
        self.failures = failures
        self.failure_status = failure_status

    @property
    def url(self) -> str:
        """Return the archive URL."""
        return f"http://127.0.0.1:{self.server_address[1]}/archive.tar.gz"


class ArchiveHandler(BaseHTTPRequestHandler):
    """Fails the first requests, then serves the archive."""

    server: ArchiveServer

    def do_GET(self) -> None:
        """Answer one download."""
        if self.server.failures:
            self.server.failures -= 1
            self.send_response(self.server.failure_status)
            self.end_headers()
            return
        self.send_response(OK)
        self.send_header("Content-Length", str(len(self.server.archive)))
        self.end_headers()
        self.wfile.write(self.server.archive)

    @override
    def log_message(self, format_: str, *args: object) -> None:
        """Silence request logging."""


def serve(archive: bytes, failures: int = 0, failure_status: int = SERVICE_UNAVAILABLE) -> ArchiveServer:
    """Start an archive server in a daemon thread."""
    server = ArchiveServer(archive, failures, failure_status)
    Thread(target=server.serve_forever, daemon=True).start()
    return server


def no_sleep(_seconds: float) -> None:
    """Skip retry waits."""


def test_fetch_retries_and_checks_the_sum() -> None:
    """Two 503s are retried; the archive matching the pin is returned."""
    archive = make_archive({f"{TOP}/{ENTRY_FILE}": BODY})
    server = serve(archive, failures=2)

    fetched = fetch(server.url, hashlib.sha256(archive).hexdigest(), sleep=no_sleep)
    server.shutdown()

    assert fetched == archive


@pytest.mark.parametrize(
    ("failures", "status", "message"),
    [(0, OK, "SHA-256"), (1, NOT_FOUND, "404"), (9, SERVICE_UNAVAILABLE, "failed 4 times")],
)
def test_fetch_refuses_bad_downloads(failures: int, status: int, message: str) -> None:
    """A wrong checksum, a 404 (not retried) and endless 503s fail the build."""
    server = serve(make_archive({f"{TOP}/{ENTRY_FILE}": BODY}), failures=failures, failure_status=status)

    with pytest.raises(ArchiveError, match=message):
        fetch(server.url, WRONG_SHA256, sleep=no_sleep)
    server.shutdown()


def test_unpack_replaces_the_target(tmp_path: Path) -> None:
    """The single top folder becomes the target; an old target is replaced."""
    target = tmp_path / "hub" / "snakers4_silero-vad_master"
    target.mkdir(parents=True)
    (target / "stale.txt").write_text("old")

    unpack_single_folder(make_archive({f"{TOP}/{ENTRY_FILE}": BODY}), target)

    assert sorted(path.name for path in target.iterdir()) == [ENTRY_FILE]


@pytest.mark.parametrize(
    ("members", "link"),
    [
        ({f"{TOP}/{ENTRY_FILE}": BODY, "other/readme.md": BODY}, None),
        ({f"{TOP}/../escape.py": BODY}, None),
        ({f"{TOP}/{ENTRY_FILE}": BODY}, "/etc/passwd"),
    ],
)
def test_unpack_refuses_unsafe_archives(tmp_path: Path, members: dict[str, bytes], link: str | None) -> None:
    """Two top folders, a `..` member and a link out of the archive are refused."""
    with pytest.raises(ArchiveError):
        unpack_single_folder(make_archive(members, link), tmp_path / "target")
