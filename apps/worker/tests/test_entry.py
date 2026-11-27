"""Flow tests of the worker entry: the pod is always terminated (architecture §5.2)."""

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from functools import partial
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import override
from unittest.mock import Mock

import pytest

from transcribe_worker.entry.config import EntryConfig
from transcribe_worker.entry.constants import (
    CHILD_TIMEOUT_EXIT_CODE,
    DEFAULT_MAX_RUNTIME_S,
    KEEP_ALIVE_POLL_S,
    MAX_RUNTIME_CEILING_S,
    RETRY_MAX_DELAY_S,
    STOP_AFTER_FAILURES,
)
from transcribe_worker.entry.pod_api import PodApiError, RunpodPodApi, terminate_until_gone
from transcribe_worker.entry.supervisor import EntryPorts, run_with_timeout, supervise
from transcribe_worker.entry.watchdog import watch

POD_ID = "pod_test123"
API_KEY = "test-key"
TERMINATE = "terminate"
STOP = "stop"
DELETE = "DELETE"
GET = "GET"
POST = "POST"
EXIT_CODE = 3
DEADLINE_S = 120
E2E_TIMEOUT_S = 60
E2E_POLL_S = 0.05
CLOCK_START_S = 5.0
ELAPSED_S = 30.0
HTTP_REDIRECT = 302


class WaitMisconfiguredError(Exception):
    """Raised by the fake wait_misconfigured, which must not block the test."""


@dataclass
class FakePodApi:
    """In-memory PodApi: terminate fails `failures` times (with `error`), then the pod is gone."""

    failures: int = 0
    error: Exception = field(default_factory=lambda: PodApiError("boom"))
    known: bool = True
    calls: list[str] = field(default_factory=list)
    gone: bool = False

    def exists(self) -> bool:
        """Return whether the pod is known."""
        return self.known

    def terminate(self) -> None:
        """Record the call; fail while failures remain."""
        self.calls.append(TERMINATE)
        if self.failures:
            self.failures -= 1
            raise self.error
        self.gone = True

    def stop(self) -> None:
        """Record the call."""
        self.calls.append(STOP)

    def is_gone(self) -> bool:
        """Return whether a terminate succeeded."""
        return self.gone


@dataclass
class FakeWatchdog:
    """Liveness of a fake watchdog."""

    running: bool = True

    def alive(self) -> bool:
        """Return whether the fake watchdog runs."""
        return self.running


@dataclass
class Recorder:
    """Fake entry ports on a fake clock; records every side effect."""

    exit_code: int = 0
    watchdog: FakeWatchdog = field(default_factory=FakeWatchdog)
    watchdog_error: OSError | None = None
    now: float = 1000.0
    events: list[str] = field(default_factory=list)
    sleeps: list[float] = field(default_factory=list)
    child_timeouts: list[float] = field(default_factory=list)

    def ports(self, run_child: Callable[[float], int] | None = None) -> EntryPorts:
        """Return the fake ports."""
        return EntryPorts(
            start_watchdog=self._start_watchdog,
            run_child=run_child or self._run_child,
            sleep=self._sleep,
            monotonic=self._monotonic,
            shield_signals=partial(self.events.append, "shield"),
            wait_misconfigured=_wait_misconfigured,
        )

    def _start_watchdog(self) -> FakeWatchdog:
        self.events.append("watchdog")
        if self.watchdog_error is not None:
            raise self.watchdog_error
        return self.watchdog

    def _run_child(self, timeout_s: float) -> int:
        self.events.append("child")
        self.child_timeouts.append(timeout_s)
        return self.exit_code

    def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def _monotonic(self) -> float:
        return self.now


def _wait_misconfigured() -> None:
    raise WaitMisconfiguredError


def _crash(_timeout_s: float) -> int:
    msg = "worker crashed"
    raise RuntimeError(msg)


def _config(*, keep_alive: bool = False, api_key: str | None = API_KEY) -> EntryConfig:
    return EntryConfig(POD_ID, api_key, "https://api.runpod.io/v2", keep_alive, DEADLINE_S)


@pytest.mark.parametrize("exit_code", [0, 1, 137])
def test_pod_ends_whatever_the_child_returns(exit_code: int) -> None:
    """Normal end, error exit or OOM kill: watchdog first, child with the deadline, terminate."""
    api = FakePodApi()
    recorder = Recorder(exit_code=exit_code)
    assert supervise(_config(), api, recorder.ports()) == exit_code
    assert recorder.events == ["watchdog", "child", "shield"]
    assert recorder.child_timeouts == [DEADLINE_S]
    assert api.calls == [TERMINATE]


def test_pod_ends_when_the_child_crashes() -> None:
    """An exception while running the child still terminates the pod."""
    api = FakePodApi()
    with pytest.raises(RuntimeError, match="worker crashed"):
        supervise(_config(), api, Recorder().ports(_crash))
    assert api.gone


def test_pod_ends_when_the_watchdog_cannot_start() -> None:
    """A failing watchdog start is logged; the worker runs and the pod is still terminated."""
    api = FakePodApi()
    recorder = Recorder(watchdog_error=OSError("fork failed"))
    supervise(_config(), api, recorder.ports())
    assert "child" in recorder.events
    assert api.gone


@pytest.mark.parametrize("error", [PodApiError("http 500"), ValueError("bad header"), IncompleteRead(b"")])
def test_terminate_never_gives_up(error: Exception) -> None:
    """Any error is retried; stop is added from the STOP_AFTER_FAILURES-th failure on."""
    failures = STOP_AFTER_FAILURES + 4
    api = FakePodApi(failures=failures, error=error)
    recorder = Recorder()
    supervise(_config(), api, recorder.ports())
    expected = [TERMINATE for _ in range(STOP_AFTER_FAILURES - 1)]
    for _ in range(failures - STOP_AFTER_FAILURES + 1):
        expected.extend((TERMINATE, STOP))
    assert api.calls == [*expected, TERMINATE]
    assert recorder.sleeps == sorted(recorder.sleeps)
    assert max(recorder.sleeps) == RETRY_MAX_DELAY_S


def test_keep_alive_terminates_at_the_deadline() -> None:
    """Test mode keeps the pod while the watchdog lives, and the entry ends it at the deadline."""
    api = FakePodApi()
    recorder = Recorder()
    supervise(_config(keep_alive=True), api, recorder.ports())
    polls = int(DEADLINE_S / KEEP_ALIVE_POLL_S)
    assert recorder.sleeps == [KEEP_ALIVE_POLL_S for _ in range(polls)]
    assert api.gone


def test_keep_alive_without_watchdog_ends_at_once() -> None:
    """If the watchdog died, the entry does not rely on it."""
    api = FakePodApi()
    recorder = Recorder(watchdog=FakeWatchdog(running=False))
    supervise(_config(keep_alive=True), api, recorder.ports())
    assert not recorder.sleeps
    assert api.gone


def test_child_gets_only_the_remaining_time() -> None:
    """The child is killed at the same deadline the watchdog uses."""
    recorder = Recorder()
    ports = recorder.ports()
    ports = EntryPorts(
        start_watchdog=ports.start_watchdog,
        run_child=ports.run_child,
        sleep=ports.sleep,
        monotonic=iter([CLOCK_START_S, *(CLOCK_START_S + ELAPSED_S for _ in range(3))]).__next__,
        shield_signals=ports.shield_signals,
        wait_misconfigured=ports.wait_misconfigured,
    )
    supervise(_config(), FakePodApi(), ports)
    assert recorder.child_timeouts == [DEADLINE_S - ELAPSED_S]


def test_unknown_pod_does_not_start_the_worker() -> None:
    """A wrong pod id or base URL is reported and the worker is not started."""
    api = FakePodApi(known=False)
    recorder = Recorder()
    supervise(_config(), api, recorder.ports())
    assert "child" not in recorder.events
    assert api.calls == [TERMINATE]


def test_pod_without_api_key_waits_instead_of_running() -> None:
    """In a pod that cannot terminate itself the worker never starts and PID 1 does not exit."""
    recorder = Recorder()
    config = _config(api_key=None)
    with pytest.raises(WaitMisconfiguredError):
        supervise(config, config.pod_api(), recorder.ports())
    assert not recorder.events


def test_outside_runpod_the_exit_code_is_returned() -> None:
    """Outside Runpod (no pod id) the entry just runs the child."""
    config = EntryConfig(None, None, "https://api.runpod.io/v2", keep_alive=False, max_runtime_s=DEADLINE_S)
    assert supervise(config, None, Recorder(exit_code=EXIT_CODE).ports()) == EXIT_CODE


def test_watchdog_terminates_after_the_deadline() -> None:
    """The watchdog sleeps for MAX_RUNTIME_S, then terminates until the pod is gone."""
    api = FakePodApi(failures=1)
    sleeps: list[float] = []
    watch(DEADLINE_S, api, sleeps.append)
    assert sleeps[0] == DEADLINE_S
    assert api.gone


def test_child_is_killed_at_the_timeout() -> None:
    """The real child runner kills a hanging worker."""
    started = time.monotonic()
    exit_code = run_with_timeout([sys.executable, "-c", "import time; time.sleep(30)"], 0.5)
    assert exit_code == CHILD_TIMEOUT_EXIT_CODE
    assert time.monotonic() - started < E2E_TIMEOUT_S / 4


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, DEFAULT_MAX_RUNTIME_S),
        ("", DEFAULT_MAX_RUNTIME_S),
        ("abc", DEFAULT_MAX_RUNTIME_S),
        ("-5", DEFAULT_MAX_RUNTIME_S),
        ("0", DEFAULT_MAX_RUNTIME_S),
        ("900", 900),
        ("1000000", MAX_RUNTIME_CEILING_S),
    ],
)
def test_invalid_max_runtime_stays_bounded(raw: str | None, expected: int) -> None:
    """A broken MAX_RUNTIME_S never disables the deadline or makes it days long."""
    env = {} if raw is None else {"MAX_RUNTIME_S": raw}
    assert EntryConfig.from_env(env).max_runtime_s == expected


@pytest.mark.parametrize(("raw", "expected"), [("true", True), (" YES ", True), ("1", True), ("false", False)])
def test_keep_pod_alive_parsing(raw: str, *, expected: bool) -> None:
    """Only explicit yes-values enable test mode."""
    assert EntryConfig.from_env({"KEEP_POD_ALIVE": raw}).keep_alive is expected


def test_config_repr_hides_the_api_key() -> None:
    """The key never shows up in a repr (assertion diffs, tracebacks with locals)."""
    assert API_KEY not in repr(_config())


@pytest.mark.parametrize(
    "base_url",
    ["http://api.runpod.io/v2", "https://evil.example/v2", "http://127.0.0.1:@evil.example/v2", "ftp://127.0.0.1/"],
)
def test_untrusted_api_urls_are_refused(base_url: str) -> None:
    """The API key only goes to runpod.io over TLS, or to the local fake API."""
    with pytest.raises(ValueError, match="refusing API base URL"):
        RunpodPodApi(base_url, API_KEY, POD_ID)


@dataclass
class FakeRunpod:
    """State of the fake Runpod API server."""

    status: str = "RUNNING"
    delete_failures: int = 0
    forced_status: int | None = None
    requests: list[tuple[str, str, str, bytes]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def respond(self, method: str, path: str, authorization: str, body: bytes) -> tuple[int, bytes]:
        """Apply one request and return the status code and body."""
        with self.lock:
            self.requests.append((method, path, authorization, body))
            return self._outcome(method)

    def methods(self) -> list[str]:
        """Return the HTTP methods received so far."""
        with self.lock:
            return [request[0] for request in self.requests]

    def _outcome(self, method: str) -> tuple[int, bytes]:
        if self.forced_status is not None:
            return self.forced_status, b""
        if method == DELETE:
            return self._delete()
        if method == POST:
            return 200, b"{}"
        if self.status == "TERMINATED":
            return 404, b""
        return 200, json.dumps({"id": POD_ID, "status": self.status}).encode()

    def _delete(self) -> tuple[int, bytes]:
        if self.delete_failures:
            self.delete_failures -= 1
            return 500, b""
        self.status = "TERMINATED"
        return 204, b""


class FakeRunpodServer(ThreadingHTTPServer):
    """HTTP server carrying the fake API state."""

    def __init__(self, state: FakeRunpod) -> None:
        """Listen on a free localhost port."""
        super().__init__(("127.0.0.1", 0), FakeRunpodHandler)
        self.state = state


class FakeRunpodHandler(BaseHTTPRequestHandler):
    """Answers every method from the server's FakeRunpod state."""

    server: FakeRunpodServer

    def do_GET(self) -> None:
        """Answer GET."""
        self._reply()

    def do_DELETE(self) -> None:
        """Answer DELETE."""
        self._reply()

    def do_POST(self) -> None:
        """Answer POST."""
        self._reply()

    @override
    def log_message(self, format_: str, *args: object) -> None:
        """Silence request logging."""

    def _reply(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        authorization = self.headers.get("Authorization", "")
        code, payload = self.server.state.respond(self.command, self.path, authorization, body)
        self.send_response(code)
        if code == HTTP_REDIRECT:
            self.send_header("Location", "http://127.0.0.1:9/elsewhere")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def fake_runpod() -> Iterator[tuple[FakeRunpod, str]]:
    """Run a fake Runpod API on localhost; yield its state and base URL."""
    state = FakeRunpod()
    server = FakeRunpodServer(state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    yield state, f"http://127.0.0.1:{port}/v2"
    server.shutdown()
    server.server_close()


def _no_sleep(_seconds: float) -> None:
    """Skip the backoff in tests."""


def test_http_client_terminates_and_verifies(fake_runpod: tuple[FakeRunpod, str]) -> None:
    """The real client: probe, a 500 is retried, DELETE then GET 404 means gone, key sent."""
    state, base_url = fake_runpod
    state.delete_failures = 1
    api = RunpodPodApi(base_url, API_KEY, POD_ID)
    assert api.exists()
    terminate_until_gone(api, sleep=_no_sleep)
    assert state.methods() == [GET, DELETE, DELETE, GET]
    assert {request[1] for request in state.requests} == {f"/v2/pods/{POD_ID}"}
    assert {request[2] for request in state.requests} == {f"Bearer {API_KEY}"}


def test_http_client_stop_fallback(fake_runpod: tuple[FakeRunpod, str]) -> None:
    """After repeated failures the real client posts the stop action."""
    state, base_url = fake_runpod
    state.delete_failures = STOP_AFTER_FAILURES
    api = RunpodPodApi(base_url, API_KEY, POD_ID)
    api.exists()
    terminate_until_gone(api, sleep=_no_sleep)
    posts = [request for request in state.requests if request[0] == POST]
    assert [(post[1], json.loads(post[3])) for post in posts] == [(f"/v2/pods/{POD_ID}/action", {"action": STOP})]


@pytest.mark.parametrize("status", ["RUNNING", "EXITED"])
def test_pod_that_still_exists_is_not_gone(fake_runpod: tuple[FakeRunpod, str], status: str) -> None:
    """Only TERMINATED or a 404 after the pod was seen counts as gone."""
    state, base_url = fake_runpod
    state.status = status
    assert not RunpodPodApi(base_url, API_KEY, POD_ID).is_gone()


@pytest.mark.parametrize(("forced", "message"), [(404, "does not know this pod"), (401, "HTTP 401"), (302, "HTTP 302")])
def test_http_failures_are_pod_api_errors(fake_runpod: tuple[FakeRunpod, str], forced: int, message: str) -> None:
    """Unknown pod (404 never seen), auth errors and redirects never look like success."""
    state, base_url = fake_runpod
    state.forced_status = forced
    with pytest.raises(PodApiError, match=message):
        RunpodPodApi(base_url, API_KEY, POD_ID).terminate()
    assert len(state.requests) == 1


def test_protocol_errors_are_pod_api_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Protocol and encoding errors from http.client become PodApiError without the message."""
    api = RunpodPodApi("https://api.runpod.io/v2", API_KEY, POD_ID)
    partial_body = b"secret"
    opener = Mock(spec=["open"])
    opener.open.side_effect = IncompleteRead(partial_body)
    monkeypatch.setattr(api._session, "_opener", opener)  # noqa: SLF001 - inject the failure below the client
    with pytest.raises(PodApiError, match="IncompleteRead") as caught:
        api.terminate()
    assert "secret" not in str(caught.value)


def _clean_env(base_url: str) -> dict[str, str]:
    """Environment of the real entry: nothing inherited except what Python needs."""
    inherited = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT") if name in os.environ}
    return inherited | {
        "RUNPOD_POD_ID": POD_ID,
        "RUNPOD_API_KEY": API_KEY,
        "RUNPOD_API_BASE_URL": base_url,
        "MAX_RUNTIME_S": "1",
    }


def _kill_watchdog(stderr: str) -> None:
    """Kill the watchdog the entry started (it runs in its own session)."""
    match = re.search(r"watchdog started \(pid (\d+)\)", stderr)
    if match is None:
        return
    try:
        os.kill(int(match.group(1)), signal.SIGKILL)
    except ProcessLookupError:
        return


def test_entry_process_terminates_the_pod(fake_runpod: tuple[FakeRunpod, str]) -> None:
    """End to end: the real entry runs the (failing) worker child and terminates the pod."""
    state, base_url = fake_runpod
    completed = subprocess.run(
        [sys.executable, "-m", "transcribe_worker.entry.supervisor"],
        env=_clean_env(base_url),
        capture_output=True,
        text=True,
        timeout=E2E_TIMEOUT_S,
        check=False,
    )
    _kill_watchdog(completed.stderr)
    assert completed.returncode == 1, completed.stderr
    assert "terminating the pod" in completed.stderr
    assert DELETE in state.methods()


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + E2E_TIMEOUT_S
    while time.monotonic() < deadline and not condition():
        time.sleep(E2E_POLL_S)


def test_sigterm_does_not_interrupt_termination(fake_runpod: tuple[FakeRunpod, str]) -> None:
    """A SIGTERM during the terminate retries is ignored; the pod is still terminated."""
    state, base_url = fake_runpod
    state.delete_failures = 1
    process = subprocess.Popen(
        [sys.executable, "-m", "transcribe_worker.entry.supervisor"],
        env=_clean_env(base_url),
        stderr=subprocess.PIPE,
        text=True,
    )
    with ExitStack() as cleanup:
        cleanup.callback(process.kill)
        _wait_until(lambda: DELETE in state.methods())
        process.send_signal(signal.SIGTERM)
        _, stderr = process.communicate(timeout=E2E_TIMEOUT_S)
    _kill_watchdog(stderr)
    assert process.returncode == 1, stderr
    assert state.status == "TERMINATED"
