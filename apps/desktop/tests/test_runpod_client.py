"""Regression tests of the Runpod client against the fake API (architecture §3.7; plan m5 M5.6).

No test talks to the real Runpod API: every request goes to `support.runpod_api` through an
`httpx.MockTransport`, and waits between retries are recorded instead of slept.
"""

import logging
from collections.abc import Callable, Mapping
from datetime import timedelta
from email.utils import format_datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from support.fakes import FIXED_NOW
from support.runpod import FULL_CREDENTIALS, NO_CREDENTIALS, R2_PRIVATE, Cloud, WaitRecorder
from support.runpod_api import FAKE_API_KEY, FAKE_GPU_ID, Failure, FakeSettings
from transcribe_ui.api.errors import CancelledError
from transcribe_ui.api.ports import CancelFlag, DiscardProgress, RequestContext
from transcribe_ui.runpod.bodies import CreatePod, GlobalMount, GpuRequest, Mounts, QueueRequest, SecretCreate
from transcribe_ui.runpod.calls import Call
from transcribe_ui.runpod.check import check_runpod
from transcribe_ui.runpod.client import RunpodClient
from transcribe_ui.runpod.constants import (
    CHECK_ATTEMPTS,
    CHECK_CONNECT_TIMEOUT_S,
    CHECK_READ_TIMEOUT_S,
    MAX_ATTEMPTS,
    POD_TERMINATED,
    QUEUED_POD_CANCELLED,
    QUEUED_POD_DEPLOYED,
    RETRY_AFTER_MAX_S,
    SCOPE_PODS_READ,
)
from transcribe_ui.runpod.errors import AmbiguousCreateError, RunpodApiError
from transcribe_ui.runpod.session import RunpodHttp
from transcribe_ui.runpod.wire import Pod, QueuedPod

GET = "GET"
POST = "POST"
PODS = "/v2/pods"
VOLUMES = "/v2/global-volumes"
VOLUME_NAME = "transcribe-ui-backups"
SERVICE_UNAVAILABLE = 503
BAD_GATEWAY = 502
INVALID_BODY = 422
FORBIDDEN = 403
UNAUTHORIZED = 401
TOO_MANY = 429
POD_COUNT = 5
PAGE_SIZE = 2
# Requests for 5 pods at 2 per page.
PAGE_REQUESTS = 3
# A failed attempt and its successful retry.
TWO_ATTEMPTS = 2
GPU_PRICE = 0.31
# How far ahead the `Retry-After` date lies.
DATE_OFFSET_S = 20
# The same as a time span.
DATE_OFFSET = timedelta(seconds=DATE_OFFSET_S)
# The delay of a `Retry-After` in the past or negative.
NO_WAIT = float(0)
POD_NAME = "transcribe-ui-01KAZ0000000000000000000000-until-20261127T1500Z"
FOREIGN_HOST = "https://attacker.example/v2/pods"


def _http_date(offset: timedelta) -> str:
    return format_datetime(FIXED_NOW + offset, usegmt=True)


def _failing(status: int) -> tuple[Failure, ...]:
    return tuple(Failure(status=status) for _ in range(MAX_ATTEMPTS))


def _raised(call: Callable[[], object]) -> RunpodApiError:
    try:
        call()
    except RunpodApiError as error:
        return error
    raise AssertionError


def _create_body(*, queued: bool) -> CreatePod:
    expires = Cloud().clock.now() + timedelta(hours=1)
    return CreatePod(
        name=POD_NAME,
        template_id="tpl0001",
        gpu=GpuRequest(gpu_id=FAKE_GPU_ID, min_cuda_version="12.8"),
        cloud="SECURE",
        disk=60,
        mounts=Mounts(global_=(GlobalMount(volume_id="gsb_0001", path="/workspace"),)),
        env={"TRANSCRIBE_RUN_ID": "01KAZ0000000000000000000000", "MAX_RUNTIME_S": "3600"},
        queue=QueueRequest(expires_at=expires) if queued else None,
    )


def test_lists_follow_the_cursor_and_drop_pod_env(caplog: pytest.LogCaptureFixture) -> None:
    """Every page is read; a pod's `env` (secrets) never reaches the model, its repr or a log."""
    caplog.set_level(logging.DEBUG)
    cloud = Cloud(settings=FakeSettings(page_size=PAGE_SIZE))
    for index in range(POD_COUNT):
        cloud.api.pods.add(f"pod-{index}", env={"R2_ACCESS": R2_PRIVATE})
    with cloud.client() as client:
        pods = client.pods.list_pods()
    assert [pod.name for pod in pods] == [f"pod-{index}" for index in reversed(range(POD_COUNT))]
    assert cloud.api.count(GET, PODS) == PAGE_REQUESTS
    assert "env" not in Pod.model_fields
    rendered = f"{pods!r} {[pod.model_dump_json() for pod in pods]} {caplog.text}"
    assert R2_PRIVATE not in rendered


def test_reads_back_off_on_server_errors() -> None:
    """A 5xx on a read is retried with growing delays."""
    cloud = Cloud()
    cloud.api.fail(GET, PODS, Failure(status=SERVICE_UNAVAILABLE), Failure(status=BAD_GATEWAY))
    with cloud.client() as client:
        assert client.pods.list_pods() == ()
    assert cloud.sleeps.delays == [1.0, 2.0]


def test_reads_give_up_after_the_last_attempt() -> None:
    """A read failing every attempt raises with the status, after `MAX_ATTEMPTS` tries."""
    cloud = Cloud()
    cloud.api.fail(GET, PODS, *_failing(SERVICE_UNAVAILABLE))
    with cloud.client() as client, pytest.raises(RunpodApiError, match="HTTP 503"):
        client.pods.list_pods()
    assert cloud.api.count(GET, PODS) == MAX_ATTEMPTS
    assert len(cloud.sleeps.delays) == MAX_ATTEMPTS - 1


def test_reads_retry_connection_failures() -> None:
    """A read whose connection fails is retried."""
    cloud = Cloud()
    cloud.api.fail(GET, PODS, Failure(connect_error=True))
    with cloud.client() as client:
        assert client.pods.list_pods() == ()
    assert cloud.api.count(GET, PODS) == TWO_ATTEMPTS


@pytest.mark.parametrize("status", [INVALID_BODY, FORBIDDEN, UNAUTHORIZED])
def test_client_errors_are_final(status: int) -> None:
    """Any 4xx but 429 is not retried; the error names the scope the call needs."""
    cloud = Cloud()
    cloud.api.fail(GET, PODS, Failure(status=status))
    with cloud.client() as client:
        error = _raised(client.pods.list_pods)
    assert (error.status, error.scope) == (status, SCOPE_PODS_READ)
    assert cloud.api.count(GET, PODS) == 1
    assert not cloud.sleeps.delays


@pytest.mark.parametrize(
    ("header", "delay"),
    [
        (_http_date(DATE_OFFSET), DATE_OFFSET.total_seconds()),
        (_http_date(-DATE_OFFSET), NO_WAIT),
        ("-5", NO_WAIT),
        ("nan", 1.0),
        ("inf", 1.0),
        ("1e308", RETRY_AFTER_MAX_S),
        ("soon", 1.0),
    ],
    ids=["date", "past-date", "negative", "nan", "infinite", "huge", "unreadable"],
)
def test_retry_after_values(header: str, delay: float) -> None:
    """Delta seconds or an HTTP date (against the injected clock), clamped; nonsense backs off."""
    cloud = Cloud()
    cloud.api.fail(GET, PODS, Failure(status=TOO_MANY, retry_after=header))
    with cloud.client() as client:
        client.pods.list_pods()
    assert cloud.sleeps.delays == [delay]


def test_rate_limits_are_waited_out_even_on_creates() -> None:
    """A 429 refused the request, so even a create is sent again after `Retry-After` (capped)."""
    cloud = Cloud()
    long_wait = Failure(status=TOO_MANY, retry_after="9999")
    cloud.api.fail(POST, VOLUMES, Failure(status=TOO_MANY, retry_after="7"), long_wait)
    with cloud.client() as client:
        client.volumes.create(VOLUME_NAME)
    assert cloud.sleeps.delays == [7.0, RETRY_AFTER_MAX_S]
    assert len(cloud.api.volumes.records) == 1


def test_create_is_retried_when_nothing_was_sent() -> None:
    """A create that never reached the API is sent again; exactly one object exists."""
    cloud = Cloud()
    cloud.api.fail(POST, VOLUMES, Failure(connect_error=True))
    with cloud.client() as client:
        client.volumes.create(VOLUME_NAME)
    assert cloud.api.count(POST, VOLUMES) == TWO_ATTEMPTS
    assert len(cloud.api.volumes.records) == 1


@pytest.mark.parametrize(
    "failure",
    [Failure(lost_response=True), Failure(status=BAD_GATEWAY), Failure(redirect_to=FOREIGN_HOST)],
    ids=["lost-response", "server-error", "redirect"],
)
def test_ambiguous_create_is_never_resent(failure: Failure) -> None:
    """A create that may have taken effect raises at once and is not sent again."""
    cloud = Cloud()
    cloud.api.fail(POST, VOLUMES, failure)
    with cloud.client() as client, pytest.raises(AmbiguousCreateError):
        client.volumes.create(VOLUME_NAME)
    assert cloud.api.count(POST, VOLUMES) == 1
    assert not cloud.sleeps.delays
    assert len(cloud.api.volumes.records) == int(failure.lost_response)


def test_unreadable_create_answer_is_ambiguous() -> None:
    """A 2xx whose body does not parse created the object: `AmbiguousCreateError`, not resent."""
    cloud = Cloud()
    cloud.api.fail(POST, VOLUMES, Failure(garbled_body=True))
    with cloud.client() as client, pytest.raises(AmbiguousCreateError, match="unexpected response shape"):
        client.volumes.create(VOLUME_NAME)
    assert (cloud.api.count(POST, VOLUMES), len(cloud.api.volumes.records)) == (1, 1)


def _chain(error: BaseException) -> list[BaseException]:
    found: list[BaseException] = []
    pending: list[BaseException | None] = [error]
    while pending:
        current = pending.pop()
        if current is not None and current not in found:
            found.append(current)
            pending.extend((current.__cause__, current.__context__))
    return found


@pytest.mark.parametrize(
    "failure",
    [Failure(lost_response=True), Failure(connect_error=True)],
    ids=["lost-response", "connection-failure"],
)
def test_errors_chain_nothing_of_the_request(failure: Failure) -> None:
    """The transport errors carry the request (the fake puts the key in their message); none is chained."""
    cloud = Cloud()
    failures = (failure,) * MAX_ATTEMPTS
    cloud.api.fail(GET, PODS, *failures)
    cloud.api.fail(POST, VOLUMES, *failures)
    with cloud.client() as client:
        errors = [_raised(client.pods.list_pods), _raised(lambda: client.volumes.create(VOLUME_NAME))]
    chained = [entry for error in errors for entry in _chain(error)]
    assert chained == errors
    rendered = [f"{entry!r} {entry.args!r}" for entry in chained]
    assert not any(FAKE_API_KEY in text for text in rendered)


def test_cancel_during_a_wait_stops_the_retries() -> None:
    """Once the cancel token is set while waiting, no further attempt is made."""
    cancel = CancelFlag()
    cloud = Cloud(sleeps=WaitRecorder(on_wait=cancel.cancel))
    cloud.api.fail(GET, PODS, *_failing(SERVICE_UNAVAILABLE))
    with cloud.client(cancel.is_cancelled) as client, pytest.raises(CancelledError):
        client.pods.list_pods()
    assert (cloud.api.count(GET, PODS), cloud.sleeps.delays) == (1, [1.0])


@pytest.mark.parametrize("name", ["RUNPOD_TOKEN", "runpod_x", "1starts_with_a_digit", "has space"])
def test_fake_refuses_secret_names_runpod_refuses(name: str) -> None:
    """The fake checks secret names as documented (pattern, reserved prefix): 422."""
    cloud = Cloud()
    with cloud.client() as client:
        error = _raised(lambda: client.secrets.create(SecretCreate(name=name, secret_value=SecretStr("x"))))
    assert error.status == INVALID_BODY


def test_redirects_are_not_followed() -> None:
    """A redirect is an error; the key never travels to the redirect target."""
    cloud = Cloud()
    cloud.api.fail(GET, PODS, Failure(redirect_to=FOREIGN_HOST))
    with cloud.client() as client, pytest.raises(RunpodApiError):
        client.pods.list_pods()
    assert [(seen.method, seen.path) for seen in cloud.api.seen()] == [(GET, PODS)]


@pytest.mark.parametrize(
    "base_url",
    [
        "https://attacker.example/v2",
        "http://api.runpod.io/v2",
        "https://api.runpod.io.attacker.example/v2",
        "https://notrunpod.io/v2",
        "https://rest.runpod.io/v2",
        "https://api.runpod.io./v2",
        "https://api.runpod.io:443/v2",
        "https://api.runpod.io:8443/v2",
        "https://user:password@api.runpod.io/v2",
    ],
)
def test_the_key_goes_only_to_the_api_host(base_url: str) -> None:
    """Only https on exactly `api.runpod.io` (no port, no trailing dot) is accepted."""
    with pytest.raises(ValueError, match=r"api\.runpod\.io"):
        RunpodClient(SecretStr(FAKE_API_KEY), base_url=base_url, transport=Cloud().api.transport())


def test_host_names_are_case_insensitive() -> None:
    """An upper-case host name is the same host."""
    cloud = Cloud()
    client = RunpodClient(SecretStr(FAKE_API_KEY), base_url="https://API.RUNPOD.IO/v2", transport=cloud.api.transport())
    with client:
        assert client.pods.list_pods() == ()


def test_every_request_is_checked() -> None:
    """An absolute foreign URL in a call is refused before it leaves (the per-request check)."""
    cloud = Cloud()
    foreign = Call(GET, FOREIGN_HOST, "pods", SCOPE_PODS_READ)
    http = RunpodHttp(SecretStr(FAKE_API_KEY), transport=cloud.api.transport())
    with http, pytest.raises(RunpodApiError, match="outside"):
        http.send(foreign)
    assert not cloud.api.seen()


def test_error_messages_hold_no_request_data() -> None:
    """Failures name the call and the status only: no URL, id, key or body."""
    cloud = Cloud()
    secret_id = cloud.api.secrets.add("transcribe_ui_r2_bucket", "old")["id"]
    cloud.api.fail("PATCH", f"/v2/account/secrets/{secret_id}", Failure(status=INVALID_BODY))
    with cloud.client() as client, pytest.raises(RunpodApiError, match=r"^PATCH secret: HTTP 422$"):
        client.secrets.set_value(secret_id, SecretStr(R2_PRIVATE))


def test_environment_proxies_never_bypass_the_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    """With an injected transport, proxy variables are ignored: tests never reach the network."""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    cloud = Cloud()
    with cloud.client() as client:
        assert client.pods.list_pods() == ()


def test_missing_objects_read_as_none() -> None:
    """A 404 on one object means it does not exist (or is gone)."""
    cloud = Cloud()
    with cloud.client() as client:
        found = (client.pods.get_pod("pod9999"), client.pods.get_queued_pod("qpod9999"))
        removed = (client.pods.terminate("pod9999"), client.pods.cancel_queued("qpod9999"))
    assert found == (None, None)
    assert removed == (False, False)


def test_pod_create_direct_and_terminate() -> None:
    """With capacity a create gives a pod (201); a terminated pod stays listed as TERMINATED."""
    cloud = Cloud()
    with cloud.client() as client:
        pod = client.pods.create(_create_body(queued=False))
        assert isinstance(pod, Pod)
        assert client.pods.terminate(pod.pod_id)
        listed = client.pods.list_pods()
    assert [(entry.name, entry.status) for entry in listed] == [(POD_NAME, POD_TERMINATED)]


def test_queued_pod_deploys_on_schedule() -> None:
    """Without capacity a create with `queue` gives a queued pod (202) that deploys later."""
    cloud = Cloud(settings=FakeSettings(capacity=False, deploy_after=timedelta(minutes=10)))
    with cloud.client() as client:
        queued = client.pods.create(_create_body(queued=True))
        assert isinstance(queued, QueuedPod)
        assert client.pods.list_pods() == ()
        cloud.clock.advance(timedelta(minutes=10))
        deployed = client.pods.get_queued_pod(queued.queued_pod_id)
        pods = client.pods.list_pods()
    assert deployed is not None
    assert (deployed.status, [pod.pod_id for pod in pods]) == (QUEUED_POD_DEPLOYED, [deployed.pod_id])


def test_cancelled_queued_pods_stay_listed() -> None:
    """A cancelled queued pod is still listed, with its terminal status."""
    cloud = Cloud(settings=FakeSettings(capacity=False))
    with cloud.client() as client:
        queued = client.pods.create(_create_body(queued=True))
        assert isinstance(queued, QueuedPod)
        assert client.pods.cancel_queued(queued.queued_pod_id)
        listed = client.pods.list_queued_pods()
    assert [entry.status for entry in listed] == [QUEUED_POD_CANCELLED]


def test_no_capacity_without_queue_is_a_plain_error() -> None:
    """A 400 (no capacity) is a definite refusal, not an ambiguous outcome."""
    cloud = Cloud(settings=FakeSettings(capacity=False))
    with cloud.client() as client:
        error = _raised(lambda: client.pods.create(_create_body(queued=False)))
    assert not isinstance(error, AmbiguousCreateError)
    assert cloud.api.count(POST, PODS) == 1


def test_catalog_price() -> None:
    """The secure price of a listed GPU type; None for an unknown one."""
    cloud = Cloud(settings=FakeSettings(gpu_price=GPU_PRICE))
    with cloud.client() as client:
        listed = client.catalog.secure_price(FAKE_GPU_ID)
        unknown = client.catalog.secure_price("NVIDIA H100")
    assert listed == pytest.approx(GPU_PRICE)
    assert unknown is None


@pytest.mark.parametrize(
    ("credentials", "failures", "expected"),
    [
        (FULL_CREDENTIALS, (), ("ok", None, 1)),
        (NO_CREDENTIALS, (), ("missing", "credentials_missing", 0)),
        ({"runpod_api_key": "rpa_WRONG"}, (), ("failed", "runpod_key_rejected", 1)),
        (FULL_CREDENTIALS, (Failure(status=FORBIDDEN),), ("failed", "runpod_key_missing_scope", 1)),
        (FULL_CREDENTIALS, _failing(SERVICE_UNAVAILABLE), ("failed", "cloud_unreachable", CHECK_ATTEMPTS)),
    ],
    ids=["ok", "missing", "rejected", "missing-scope", "unreachable"],
)
def test_key_check(
    tmp_path: Path,
    credentials: Mapping[str, str],
    failures: tuple[Failure, ...],
    expected: tuple[str, str | None, int],
) -> None:
    """The Runpod half of `check_credentials`: one pod-list entry read (never parsed), at most 2 attempts."""
    cloud = Cloud(credentials=credentials)
    cloud.api.fail(GET, PODS, *failures)
    context = RequestContext(cloud.ports(tmp_path), DiscardProgress(), CancelFlag())
    checked = check_runpod(context)
    outcome = (checked.outcome, checked.code, cloud.api.count(GET, PODS))
    assert (checked.service, outcome) == ("runpod", expected)
    assert all(seen.query == "limit=1" for seen in cloud.api.seen())


def test_key_check_bounds_rate_limit_waits(tmp_path: Path) -> None:
    """A 429 asking for 9999 s waits at most 1 s; 2 attempts with 5 s / 10 s timeouts (about 30 s)."""
    cloud = Cloud()
    cloud.api.fail(GET, PODS, *(Failure(status=TOO_MANY, retry_after="9999"),) * CHECK_ATTEMPTS)
    context = RequestContext(cloud.ports(tmp_path), DiscardProgress(), CancelFlag())
    checked = check_runpod(context)
    assert (checked.outcome, checked.code, cloud.sleeps.delays) == ("failed", "cloud_unreachable", [1.0])
    seen = cloud.api.seen()
    assert len(seen) == CHECK_ATTEMPTS
    assert {request.timeouts for request in seen} == {(CHECK_CONNECT_TIMEOUT_S, CHECK_READ_TIMEOUT_S)}


def test_key_check_stops_on_cancel(tmp_path: Path) -> None:
    """The check's client gets the request's cancel token: a cancel during its wait ends it."""
    cancel = CancelFlag()
    cloud = Cloud(sleeps=WaitRecorder(on_wait=cancel.cancel))
    cloud.api.fail(GET, PODS, *_failing(SERVICE_UNAVAILABLE))
    context = RequestContext(cloud.ports(tmp_path), DiscardProgress(), cancel)
    with pytest.raises(CancelledError):
        check_runpod(context)
    assert cloud.api.count(GET, PODS) == 1


def test_non_ascii_key_never_reaches_the_client() -> None:
    """A non-ASCII key (which httpx cannot encode) is refused before any request."""
    cloud = Cloud()
    with pytest.raises(ValueError, match="ASCII"):
        RunpodClient(SecretStr("rpa_KEY\u00e9"), transport=cloud.api.transport())
    assert not cloud.api.seen()
