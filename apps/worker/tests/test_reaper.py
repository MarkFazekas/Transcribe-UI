"""Flow tests of the external pod reaper (architecture §5.2, layer 4) against a fake Runpod API."""

import json
import logging
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import override
from urllib.parse import parse_qs, urlsplit

import pytest

from transcribe_contracts.pods import pod_name
from transcribe_worker.reaper.api import RunpodReaperApi
from transcribe_worker.reaper.constants import (
    CALL_ATTEMPTS,
    CONFIRM_DELAY_S,
    ENV_API_BASE_URL,
    ENV_API_KEY,
    GRACE,
    MAX_DEADLINE_SPAN,
    MAX_PAGES,
    PASS_BUDGET,
    QUEUED_PHASE_BUDGET,
    RETRY_INITIAL_DELAY_S,
)
from transcribe_worker.reaper.main import main
from transcribe_worker.reaper.reap import ReaperPorts, reap

API_KEY = "reaper-test-key"
NOW = datetime.fromisoformat("2026-11-27T12:00:00+00:00")
CREATED_AT = (NOW - timedelta(hours=1)).isoformat()
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
STATUS = "status"
PODS = "/v2/pods"
QUEUED_PODS = "/v2/queued-pods"
HTTP_OK = 200
HTTP_NO_CONTENT = 204
HTTP_NOT_FOUND = 404
HTTP_SERVER_ERROR = 500
P_OVERDUE = "p_overdue"
Q_OVERDUE = "q_overdue"
P_BROKEN = "p_broken"
Q_OVERDUE_PATH = f"{QUEUED_PODS}/{Q_OVERDUE}"
P_OVERDUE_PATH = f"{PODS}/{P_OVERDUE}"
TICK = timedelta(seconds=1)
ID_MAX_LENGTH = 64
AUDIT_LINE = f"pod={P_OVERDUE} run={RUN_ID} deadline=2026-11-27T11:49:00+00:00 action=terminate outcome=done"


@dataclass
class FakeClock:
    """Fake clock and sleep; `step` is added to the time on every reading."""

    now: datetime = NOW
    step: timedelta = timedelta(0)
    sleeps: list[float] = field(default_factory=list)

    def ports(self) -> ReaperPorts:
        """Return the reaper ports on this clock."""
        return ReaperPorts(now=self._now, sleep=self.sleeps.append)

    def advance(self, delta: timedelta) -> None:
        """Move the time forward."""
        self.now += delta

    def _now(self) -> datetime:
        reading = self.now
        self.now += self.step
        return reading


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
    scripted: dict[tuple[str, str], list[int]] = field(default_factory=dict)
    on_delete: Callable[[str], None] | None = None
    requests: list[tuple[str, str, str]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_pod(self, pod_id: str, name: str, status: str = RUNNING, created_at: str | None = CREATED_AT) -> None:
        """Add a pod (without `createdAt` if it is None)."""
        pod: dict[str, object] = {"id": pod_id, "name": name, STATUS: status, "env": {"R2_SECRET": "x"}}
        if created_at is not None:
            pod["createdAt"] = created_at
        self.pods[pod_id] = pod

    def add_queued(self, queued_id: str, name: str, status: str = QUEUED) -> None:
        """Add a queued pod."""
        self.queued[queued_id] = {
            "id": queued_id,
            "name": name,
            STATUS: status,
            "podId": None,
            "createdAt": CREATED_AT,
        }

    def add_overdue_pod(self) -> None:
        """Add the running overdue pod P_OVERDUE."""
        self.add_pod(P_OVERDUE, OVERDUE)

    def script(self, method: str, path: str, code: int, *, times: int = 1) -> None:
        """Answer the next `times` requests to `method path` with this status code (empty body)."""
        self.scripted[method, path] = [code for _ in range(times)]

    def reap(self, clock: FakeClock | None = None, *, dry_run: bool = False) -> bool:
        """Run one reaper pass against this fake API."""
        api = RunpodReaperApi(self.base_url, API_KEY)
        return reap(api, (clock or FakeClock()).ports(), dry_run=dry_run)

    def respond(self, method: str, target: str, authorization: str) -> tuple[int, bytes]:
        """Apply one request and return the status code and body."""
        with self.lock:
            parts = urlsplit(target)
            self.requests.append((method, parts.path, authorization))
            scripted = self.scripted.get((method, parts.path))
            if scripted:
                return scripted.pop(0), b""
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
        return self.paths(DELETE)

    def paths(self, method: str) -> list[str]:
        """Return the paths of the requests with this method received."""
        with self.lock:
            return [path for sent, path, _ in self.requests if sent == method]

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
                collection[item_id][STATUS] = "CANCELLED"
        if self.on_delete is not None:
            self.on_delete(item_id)
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
        ok = state.reap()
    assert (ok, state.deletes()) == (True, [f"{QUEUED_PODS}/{Q_OVERDUE}", f"{PODS}/{P_OVERDUE}", f"{PODS}/p_exited"])
    assert (set(state.pods), state.queued[Q_OVERDUE][STATUS]) == (
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
    state.add_overdue_pod()
    assert not state.reap()
    assert state.deletes() == [f"{PODS}/{P_OVERDUE}"]


@pytest.mark.parametrize(("failure", "attempts"), [("failing_deletes", CALL_ATTEMPTS), ("sticky_deletes", 1)])
def test_failed_terminate_fails_the_pass(fake_runpod: FakeRunpod, failure: str, attempts: int) -> None:
    """A DELETE that keeps failing, or a pod that stays, fails the pass; the next pod is still handled."""
    state = fake_runpod
    state.add_pod(P_BROKEN, OVERDUE)
    state.add_overdue_pod()
    getattr(state, failure).add(P_BROKEN)
    clock = FakeClock()
    assert not state.reap(clock)
    assert set(state.pods) == {P_BROKEN}
    assert state.deletes().count(f"{PODS}/{P_BROKEN}") == attempts
    assert clock.sleeps


def test_dry_run_sends_no_delete(fake_runpod: FakeRunpod, caplog: pytest.LogCaptureFixture) -> None:
    """A dry run only reports."""
    state = fake_runpod
    state.add_overdue_pod()
    state.add_queued(Q_OVERDUE, OVERDUE)
    with caplog.at_level(logging.INFO):
        assert state.reap(dry_run=True)
    assert not state.deletes()
    assert "action=terminate outcome=skipped (dry run)" in caplog.text


def test_unexpected_list_shape_fails(fake_runpod: FakeRunpod, caplog: pytest.LogCaptureFixture) -> None:
    """A list response that does not match the spec fails the pass; its content is not logged."""
    state = fake_runpod
    state.list_body = b'{"pods": [{"name": "leaked-secret-value"}]}'
    assert not state.reap()
    assert not state.deletes()
    assert "unexpected response shape" in caplog.text
    assert "leaked-secret-value" not in caplog.text


@pytest.mark.parametrize(
    ("advance", "expected_deletes", "pods_listed"),
    [
        # The queued phase stops after its own budget; the pod phase still runs to PASS_BUDGET.
        (QUEUED_PHASE_BUDGET + TICK, ["q1", "p1", "p2", "p3"], True),
        (PASS_BUDGET / 2 + TICK, ["q1", "p1"], True),
        # A pass used up during the queued phase does not even list pods.
        (PASS_BUDGET + TICK, ["q1"], False),
    ],
)
def test_pass_stops_when_its_budget_is_used(
    fake_runpod: FakeRunpod,
    advance: timedelta,
    expected_deletes: list[str],
    pods_listed: bool,  # noqa: FBT001 - pytest parameter
) -> None:
    """Past a phase's budget no new action starts; the rest is left for the next run."""
    state = fake_runpod
    clock = FakeClock()
    state.on_delete = lambda _item_id: clock.advance(advance)
    for index in (1, 2):
        state.add_queued(f"q{index}", OVERDUE)
    for index in (1, 2, 3):
        state.add_pod(f"p{index}", OVERDUE)
    assert not state.reap(clock)
    assert [path.rsplit("/", 1)[-1] for path in state.deletes()] == expected_deletes
    assert (PODS in state.paths(GET)) == pods_listed


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


def test_transient_failures_are_retried(fake_runpod: FakeRunpod) -> None:
    """A list and a DELETE that fail once succeed on the retry; the pass is ok."""
    state = fake_runpod
    state.add_overdue_pod()
    state.script(GET, PODS, HTTP_SERVER_ERROR)
    state.script(DELETE, P_OVERDUE_PATH, HTTP_SERVER_ERROR)
    clock = FakeClock()
    assert state.reap(clock)
    assert (state.paths(GET).count(PODS), state.deletes(), set(state.pods)) == (
        2,
        [P_OVERDUE_PATH, P_OVERDUE_PATH],
        set(),
    )
    assert clock.sleeps == [RETRY_INITIAL_DELAY_S, RETRY_INITIAL_DELAY_S]


@pytest.mark.parametrize(
    ("pagination", "error"),
    [
        ({"hasNextPage": True}, "next page announced without a cursor"),
        ({"hasNextPage": True, "nextCursor": "same"}, f"more than {MAX_PAGES} pages"),
    ],
)
def test_broken_pagination_fails(
    fake_runpod: FakeRunpod,
    caplog: pytest.LogCaptureFixture,
    pagination: dict[str, object],
    error: str,
) -> None:
    """A next page without a cursor, or a cursor that never ends, fails the pass after a bounded read."""
    state = fake_runpod
    state.list_body = json.dumps({"pods": [], "queuedPods": [], "pagination": pagination}).encode()
    assert not state.reap()
    assert not state.deletes()
    assert error in caplog.text
    assert len(state.paths(GET)) <= 2 * CALL_ATTEMPTS * MAX_PAGES


def test_missing_list_endpoint_fails(fake_runpod: FakeRunpod, caplog: pytest.LogCaptureFixture) -> None:
    """A 404 on a list endpoint is an error (wrong base URL), not an empty list."""
    state = fake_runpod
    state.add_overdue_pod()
    state.script(GET, QUEUED_PODS, HTTP_NOT_FOUND, times=CALL_ATTEMPTS)
    assert not state.reap()
    assert state.deletes() == [P_OVERDUE_PATH]
    assert "list queued pods failed: GET queued pods: HTTP 404" in caplog.text


def test_deployed_during_cancel_is_terminated(fake_runpod: FakeRunpod) -> None:
    """A queue entry that deploys while being cancelled is terminated from the pod list read afterwards."""
    state = fake_runpod
    state.add_queued(Q_OVERDUE, OVERDUE)
    state.on_delete = partial(_deploy_on_cancel, state)
    assert state.reap()
    assert (state.deletes(), set(state.pods)) == ([Q_OVERDUE_PATH, P_OVERDUE_PATH], set())


def test_pod_listed_as_terminated_counts_as_gone(fake_runpod: FakeRunpod) -> None:
    """Confirmation also accepts a pod that stays known with status TERMINATED instead of a 404."""
    state = fake_runpod
    state.add_overdue_pod()
    state.on_delete = lambda item_id: state.add_pod(item_id, OVERDUE, "TERMINATED")
    clock = FakeClock()
    assert state.reap(clock)
    assert (state.deletes(), clock.sleeps) == ([P_OVERDUE_PATH], [])


def test_failed_confirmation_check_is_retried(fake_runpod: FakeRunpod, caplog: pytest.LogCaptureFixture) -> None:
    """A confirmation GET that fails is logged and the next check confirms."""
    state = fake_runpod
    state.add_overdue_pod()
    state.script(GET, P_OVERDUE_PATH, HTTP_SERVER_ERROR)
    clock = FakeClock()
    assert state.reap(clock)
    assert (state.deletes(), clock.sleeps) == ([P_OVERDUE_PATH], [CONFIRM_DELAY_S])
    assert "confirmation check failed: GET pod: HTTP 500" in caplog.text


def test_sticky_queued_cancel_fails(fake_runpod: FakeRunpod) -> None:
    """A queued pod that stays QUEUED after the cancel fails the pass; overdue pods are still terminated."""
    state = fake_runpod
    state.add_queued(Q_OVERDUE, OVERDUE)
    state.add_overdue_pod()
    state.sticky_deletes.add(Q_OVERDUE)
    assert not state.reap()
    assert (state.deletes(), state.queued[Q_OVERDUE][STATUS], set(state.pods)) == (
        [Q_OVERDUE_PATH, P_OVERDUE_PATH],
        QUEUED,
        set(),
    )


def _deploy_on_cancel(state: FakeRunpod, item_id: str) -> None:
    if item_id == Q_OVERDUE:
        state.queued[Q_OVERDUE][STATUS] = "DEPLOYED"
        state.add_overdue_pod()


FAR_DEADLINE = pod_name(RUN_ID, datetime.max.replace(second=0, microsecond=0, tzinfo=UTC))
EDGE_DEADLINE = pod_name(RUN_ID, NOW + MAX_DEADLINE_SPAN - timedelta(minutes=1))
YEAR_ONE = datetime(1, 1, 1, tzinfo=UTC).isoformat()
LONG_AGO = (NOW - MAX_DEADLINE_SPAN).isoformat()


@pytest.mark.parametrize(
    ("name", "created_at", "plausible"),
    [
        (FAR_DEADLINE, CREATED_AT, False),
        (IN_TIME, LONG_AGO, False),
        (IN_TIME, YEAR_ONE, False),
        # Without createdAt, or with one in the future, the check measures from the pass start.
        (EDGE_DEADLINE, None, True),
        (IN_TIME, "9999-12-31T23:59:59+00:00", True),
        # An overdue pod is terminated whatever its creation time.
        (OVERDUE, YEAR_ONE, True),
    ],
)
def test_implausible_deadline_is_left_alone(
    fake_runpod: FakeRunpod,
    caplog: pytest.LogCaptureFixture,
    name: str,
    created_at: str | None,
    plausible: bool,  # noqa: FBT001 - pytest parameter
) -> None:
    """A deadline far beyond the longest legitimate run is kept, logged and fails the pass; nothing crashes."""
    state = fake_runpod
    state.add_pod("p_checked", name, created_at=created_at)
    state.add_overdue_pod()
    assert state.reap() == plausible
    assert P_OVERDUE_PATH in state.deletes()
    assert ("implausibly far after creation" in caplog.text) != plausible


@pytest.mark.parametrize("item_id", ["../escape", "id with space", "p" * (ID_MAX_LENGTH + 1), ""])
def test_unexpected_id_fails_the_page(fake_runpod: FakeRunpod, caplog: pytest.LogCaptureFixture, item_id: str) -> None:
    """An id outside the strict pattern fails its list; nothing of that page is acted on or echoed."""
    state = fake_runpod
    state.add_pod(item_id, OVERDUE)
    state.add_overdue_pod()
    assert not state.reap()
    assert not state.deletes()
    assert "GET pods: unexpected response shape" in caplog.text


def test_names_cannot_inject_workflow_commands(fake_runpod: FakeRunpod, caplog: pytest.LogCaptureFixture) -> None:
    """A malformed app name with a newline is logged escaped, so no log line starts with `::`."""
    state = fake_runpod
    state.add_pod("p_malformed", f"{MALFORMED}\n::error::injected")
    with caplog.at_level(logging.INFO):
        assert not state.reap()
    assert not any(line.startswith("::") for line in caplog.text.splitlines())
    assert "::error::injected" in caplog.text
