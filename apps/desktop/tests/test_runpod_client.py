"""Regression tests of the Runpod client against the fake API (architecture §3.7; plan m5 M5.6).

No test talks to the real Runpod API: every request goes to `support.runpod_api` through an
`httpx.MockTransport`, and waits between retries are recorded instead of slept.
"""

import logging
from collections.abc import Callable
from datetime import timedelta

import pytest
from pydantic import SecretStr

from support.runpod import R2_PRIVATE, Cloud
from support.runpod_api import FAKE_API_KEY, FAKE_GPU_ID, Failure, FakeSettings
from transcribe_ui.runpod.access import StaticCredentials
from transcribe_ui.runpod.bodies import CreatePod, GlobalMount, GpuRequest, Mounts, QueueRequest
from transcribe_ui.runpod.check import check_runpod_key
from transcribe_ui.runpod.classes import RunpodKeyState
from transcribe_ui.runpod.client import RunpodClient
from transcribe_ui.runpod.constants import (
    MAX_ATTEMPTS,
    POD_TERMINATED,
    QUEUED_POD_CANCELLED,
    QUEUED_POD_DEPLOYED,
    RETRY_AFTER_MAX_S,
    SCOPE_PODS_READ,
)
from transcribe_ui.runpod.errors import AmbiguousCreateError, RunpodApiError
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
POD_NAME = "transcribe-ui-01KAZ0000000000000000000000-until-20261127T1500Z"
FOREIGN_HOST = "https://attacker.example/v2/pods"


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
        "https://user:password@api.runpod.io/v2",
    ],
)
def test_the_key_goes_only_to_runpod(base_url: str) -> None:
    """A base URL other than https on a `*.runpod.io` host is refused before any request."""
    with pytest.raises(ValueError, match=r"runpod\.io"):
        RunpodClient(SecretStr(FAKE_API_KEY), base_url=base_url, transport=Cloud().api.transport())


def test_error_messages_hold_no_request_data() -> None:
    """Failures name the call and the status only: no URL, id, key or body."""
    cloud = Cloud()
    cloud.api.fail("PATCH", "/v2/account/secrets/sec0001", Failure(status=INVALID_BODY))
    cloud.api.secrets.add("transcribe_ui_r2_bucket", "old")
    with cloud.client() as client, pytest.raises(RunpodApiError, match=r"^PATCH secret: HTTP 422$"):
        client.secrets.set_value("sec0001", SecretStr(R2_PRIVATE))


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
    ("credentials", "failures", "state"),
    [
        (None, (), "ok"),
        (StaticCredentials(), (), "missing"),
        (StaticCredentials(api_key=SecretStr("rpa_WRONG")), (), "rejected"),
        (None, (Failure(status=FORBIDDEN),), "missing_scope"),
        (None, _failing(SERVICE_UNAVAILABLE), "unreachable"),
    ],
)
def test_key_check(credentials: StaticCredentials | None, failures: tuple[Failure, ...], state: RunpodKeyState) -> None:
    """The Runpod half of `check_credentials`: one pod-list entry read, never parsed."""
    cloud = Cloud() if credentials is None else Cloud(credentials=credentials)
    cloud.api.fail(GET, PODS, *failures)
    checked = check_runpod_key(cloud.access())
    assert checked.state == state
    expected_scope = SCOPE_PODS_READ if state == "missing_scope" else None
    assert checked.scope == expected_scope
    assert all(seen.query == "limit=1" for seen in cloud.api.seen())
