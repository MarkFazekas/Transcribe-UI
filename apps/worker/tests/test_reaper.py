"""Flow tests of the external pod reaper (architecture §5.2, layer 4) against a fake Runpod API."""

import json
import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import override
from urllib.parse import parse_qs, urlsplit

import pytest

from transcribe_contracts.pods import pod_name
from transcribe_worker.reaper.api import RunpodReaperApi
from transcribe_worker.reaper.constants import CALL_ATTEMPTS, ENV_API_BASE_URL, ENV_API_KEY, GRACE, PASS_BUDGET
from transcribe_worker.reaper.main import main
from transcribe_worker.reaper.reap import ReaperPorts, reap

API_KEY = "reaper-test-key"
NOW = datetime.fromisoformat("2026-11-27T12:00:00+00:00")
RUN_ID = "01JDA4B7C8D9E0F1G2H3J4K5M6"
OVERDUE = pod_name(RUN_ID, NOW - GRACE - timedelta(minutes=1))
IN_GRACE = pod_name(RUN_ID, NOW - GRACE)
IN_TIME = pod_name(RUN_ID, NOW + timedelta(hours=1))
MALFORMED = "transcribe-ui-manual-test"
FOREIGN = "someone-elses-training-pod"
DELETE = "DELETE"
GET = "GET"
RUNNING = "RUNNING"
QUEUED = "QUEUED"
PODS = "/v2/pods"
QUEUED_PODS = "/v2/queued-pods"
HTTP_OK = 200
HTTP_NO_CONTENT = 204
HTTP_NOT_FOUND = 404
HTTP_SERVER_ERROR = 500
P_OVERDUE = "p_overdue"
Q_OVERDUE = "q_overdue"
P_BROKEN = "p_broken"
BUDGET_STEP = PASS_BUDGET / 2 + timedelta(seconds=1)
AUDIT_LINE = f"pod={P_OVERDUE} run={RUN_ID} deadline=2026-11-27T11:49:00+00:00 action=terminate outcome=done"


@dataclass
class FakeRunpod:
    """State of the fake API: pods and queued pods by id, paginated `page_size` per page."""

    base_url: str = ""
    pods: dict[str, dict[str, object]] = field(default_factory=dict)
    queued: dict[str, dict[str, object]] = field(default_factory=dict)
    page_size: int = 2
    failing_deletes: set[str] = field(default_factory=set)
    sticky_deletes: set[str] = field(default_factory=set)
    list_body: bytes | None = None
    requests: list[tuple[str, str, str]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_pod(self, pod_id: str, name: str, status: str = RUNNING) -> None:
        """Add a pod."""
        self.pods[pod_id] = {"id": pod_id, "name": name, "status": status, "env": {"R2_SECRET": "x"}}

    def add_queued(self, queued_id: str, name: str, status: str = QUEUED) -> None:
        """Add a queued pod."""
        self.queued[queued_id] = {"id": queued_id, "name": name, "status": status, "podId": None}

    def respond(self, method: str, target: str, authorization: str) -> tuple[int, bytes]:
        """Apply one request and return the status code and body."""
        with self.lock:
            parts = urlsplit(target)
            self.requests.append((method, parts.path, authorization))
            collection, list_key = (
                (self.queued, "queuedPods") if parts.path.startswith(QUEUED_PODS) else (self.pods, "pods")
            )
            if parts.path in {PODS, QUEUED_PODS}:
                return self._page(collection, list_key, parts.query)
            item_id = parts.path.rsplit("/", 1)[-1]
            if method == DELETE:
                return self._delete(collection, item_id)
            entry = collection.get(item_id)
            return (HTTP_NOT_FOUND, b"") if entry is None else (HTTP_OK, json.dumps(entry).encode())

    def deletes(self) -> list[str]:
        """Return the paths of the DELETE requests received."""
        with self.lock:
            return [path for method, path, _ in self.requests if method == DELETE]

    def _page(self, collection: dict[str, dict[str, object]], list_key: str, query: str) -> tuple[int, bytes]:
        if self.list_body is not None:
            return HTTP_OK, self.list_body
        start = int(parse_qs(query).get("cursor", ["0"])[0])
        entries = list(collection.values())
        end = start + self.page_size
        more = end < len(entries)
        pagination = {"nextCursor": str(end) if more else None, "hasNextPage": more}
        return HTTP_OK, json.dumps({list_key: entries[start:end], "pagination": pagination}).encode()

    def _delete(self, collection: dict[str, dict[str, object]], item_id: str) -> tuple[int, bytes]:
        if item_id in self.failing_deletes:
            return HTTP_SERVER_ERROR, b""
        if item_id in collection and item_id not in self.sticky_deletes:
            if collection is self.pods:
                collection.pop(item_id)  # a terminated pod no longer appears (spec)
            else:
                collection[item_id]["status"] = "CANCELLED"
        return HTTP_NO_CONTENT, b""


class FakeRunpodServer(ThreadingHTTPServer):
    """HTTP server carrying the fake API state."""

    def __init__(self, state: FakeRunpod) -> None:
        """Listen on a free localhost port."""
        super().__init__(("127.0.0.1", 0), FakeRunpodHandler)
        self.state = state


class FakeRunpodHandler(BaseHTTPRequestHandler):
    """Answers GET and DELETE from the server's FakeRunpod state."""

    server: FakeRunpodServer

    def do_GET(self) -> None:
        """Answer GET."""
        self._reply()

    def do_DELETE(self) -> None:
        """Answer DELETE."""
        self._reply()

    @override
    def log_message(self, format_: str, *args: object) -> None:
        """Silence request logging."""

    def _reply(self) -> None:
        authorization = self.headers.get("Authorization", "")
        code, payload = self.server.state.respond(self.command, self.path, authorization)
        self.send_response(code)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def fake_runpod() -> Iterator[FakeRunpod]:
    """Run a fake Runpod API on localhost; yield its state (which knows its base URL)."""
    state = FakeRunpod()
    server = FakeRunpodServer(state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.base_url = f"http://127.0.0.1:{server.server_address[1]}/v2"
    yield state
    server.shutdown()
    server.server_close()


@dataclass
class FakeClock:
    """Fake clock and sleep; `step` is added to the time on every reading."""

    now: datetime = NOW
    step: timedelta = timedelta(0)
    sleeps: list[float] = field(default_factory=list)

    def ports(self) -> ReaperPorts:
        """Return the reaper ports on this clock."""
        return ReaperPorts(now=self._now, sleep=self.sleeps.append)

    def _now(self) -> datetime:
        reading = self.now
        self.now += self.step
        return reading


def _run(base_url: str, clock: FakeClock | None = None, *, dry_run: bool = False) -> bool:
    api = RunpodReaperApi(base_url, API_KEY)
    return reap(api, (clock or FakeClock()).ports(), dry_run=dry_run)


def _populate(state: FakeRunpod) -> None:
    for pod_id, name, status in (
        (P_OVERDUE, OVERDUE, RUNNING),
        ("p_exited", OVERDUE, "EXITED"),
        ("p_in_time", IN_TIME, RUNNING),
        ("p_in_grace", IN_GRACE, RUNNING),
        ("p_foreign", FOREIGN, RUNNING),
    ):
        state.add_pod(pod_id, name, status)
    for queued_id, name, status in (
        (Q_OVERDUE, OVERDUE, QUEUED),
        ("q_in_time", IN_TIME, QUEUED),
        ("q_expired", OVERDUE, "EXPIRED"),
        ("q_foreign", FOREIGN, QUEUED),
    ):
        state.add_queued(queued_id, name, status)


def test_full_pass(fake_runpod: FakeRunpod, caplog: pytest.LogCaptureFixture) -> None:
    """Overdue pod and queued pod removed; in time, in grace, foreign and final ones untouched."""
    state = fake_runpod
    _populate(state)
    with caplog.at_level(logging.INFO):
        ok = _run(state.base_url)
    assert (ok, state.deletes()) == (True, [f"{QUEUED_PODS}/{Q_OVERDUE}", f"{PODS}/{P_OVERDUE}", f"{PODS}/p_exited"])
    assert (set(state.pods), state.queued[Q_OVERDUE]["status"]) == (
        {"p_in_time", "p_in_grace", "p_foreign"},
        "CANCELLED",
    )
    assert {request[2] for request in state.requests} == {f"Bearer {API_KEY}"}
    log = caplog.text
    assert (FOREIGN in log, "R2_SECRET" in log, AUDIT_LINE in log) == (False, False, True)


def test_malformed_app_name_fails_the_pass(fake_runpod: FakeRunpod) -> None:
    """A name with the prefix that does not parse is left alone; the run exits non-zero."""
    state = fake_runpod
    state.add_pod("p_malformed", MALFORMED)
    state.add_pod(P_OVERDUE, OVERDUE)
    assert not _run(state.base_url)
    assert state.deletes() == [f"{PODS}/{P_OVERDUE}"]


@pytest.mark.parametrize(("failure", "attempts"), [("failing_deletes", CALL_ATTEMPTS), ("sticky_deletes", 1)])
def test_failed_terminate_fails_the_pass(fake_runpod: FakeRunpod, failure: str, attempts: int) -> None:
    """A DELETE that keeps failing, or a pod that stays, fails the pass; the next pod is still handled."""
    state = fake_runpod
    state.add_pod(P_BROKEN, OVERDUE)
    state.add_pod(P_OVERDUE, OVERDUE)
    getattr(state, failure).add(P_BROKEN)
    clock = FakeClock()
    assert not _run(state.base_url, clock)
    assert set(state.pods) == {P_BROKEN}
    assert state.deletes().count(f"{PODS}/{P_BROKEN}") == attempts
    assert clock.sleeps


def test_dry_run_sends_no_delete(fake_runpod: FakeRunpod, caplog: pytest.LogCaptureFixture) -> None:
    """A dry run only reports."""
    state = fake_runpod
    state.add_pod(P_OVERDUE, OVERDUE)
    state.add_queued(Q_OVERDUE, OVERDUE)
    with caplog.at_level(logging.INFO):
        assert _run(state.base_url, dry_run=True)
    assert not state.deletes()
    assert "action=terminate outcome=skipped (dry run)" in caplog.text


def test_unexpected_list_shape_fails(fake_runpod: FakeRunpod, caplog: pytest.LogCaptureFixture) -> None:
    """A list response that does not match the spec fails the pass; its content is not logged."""
    state = fake_runpod
    state.list_body = b'{"pods": [{"name": "leaked-secret-value"}]}'
    assert not _run(state.base_url)
    assert not state.deletes()
    assert "unexpected response shape" in caplog.text
    assert "leaked-secret-value" not in caplog.text


def test_pass_stops_when_its_budget_is_used(fake_runpod: FakeRunpod) -> None:
    """Past the pass budget no new action starts; the rest is left for the next run."""
    state = fake_runpod
    state.add_pod("p_first", OVERDUE)
    state.add_pod("p_second", OVERDUE)
    assert not _run(state.base_url, FakeClock(step=BUDGET_STEP))
    assert len(state.deletes()) == 1


def test_command_line_without_key_fails(fake_runpod: FakeRunpod, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a key the command fails before any request."""
    state = fake_runpod
    monkeypatch.setenv(ENV_API_BASE_URL, state.base_url)
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    assert main(["--dry-run"]) == 1
    assert not state.requests


def test_command_line(fake_runpod: FakeRunpod, monkeypatch: pytest.MonkeyPatch) -> None:
    """The entry point reads key and base URL from the environment; exit 1 on a malformed name."""
    state = fake_runpod
    monkeypatch.setenv(ENV_API_BASE_URL, state.base_url)
    monkeypatch.setenv(ENV_API_KEY, API_KEY)
    state.add_pod(P_OVERDUE, pod_name(RUN_ID, datetime.now(UTC) - 2 * GRACE))
    dry_clean = main(["--dry-run"])
    state.add_pod("p_malformed", MALFORMED)
    dry_malformed = main(["--dry-run"])
    deletes_after_dry_runs = state.deletes()
    assert (dry_clean, dry_malformed, deletes_after_dry_runs) == (0, 1, [])
    assert main([]) == 1
    assert state.deletes() == [f"{PODS}/{P_OVERDUE}"]


@pytest.mark.parametrize("base_url", ["http://api.runpod.io/v2", "https://evil.example/v2"])
def test_untrusted_api_urls_are_refused(monkeypatch: pytest.MonkeyPatch, base_url: str) -> None:
    """The reaper key only goes to runpod.io over TLS, or to the local fake API."""
    monkeypatch.setenv(ENV_API_KEY, API_KEY)
    monkeypatch.setenv(ENV_API_BASE_URL, base_url)
    assert main([]) == 1
