"""`launch_worker` refusals (plan m5 M5.11): active runs, stragglers, setup, nothing to do, Runpod refusing.

Terminal entries the Runpod lists keep never refuse a launch.
"""

from datetime import timedelta

import httpx
import pytest

from support.launch import (
    CATALOG,
    LAUNCH,
    PODS,
    POST,
    error_code,
    error_of,
    launch_confirmed,
    launch_run,
    plan_fingerprint,
    plan_of,
    posts,
    run_ids,
    run_status,
    send,
    set_up_cloud,
)
from support.runpod_api import Failure
from support.runs import OUTAGE, RUN_ID, RunsWorld, app_pod_name, batch_state, seed_run, seed_uploaded
from transcribe_ui.runpod.constants import (
    ERROR_CLOUD_NOT_SET_UP,
    ERROR_CLOUD_UNREACHABLE,
    POD_TERMINATED,
    QUEUED_POD_CANCELLED,
    QUEUED_POD_DEPLOYED,
    QUEUED_POD_EXPIRED,
    QUEUED_POD_FAILED,
)
from transcribe_ui.runs.constants import (
    ERROR_LAUNCH_REFUSED,
    ERROR_NOTHING_TO_LAUNCH,
    ERROR_RUN_ACTIVE,
    FALLBACK_PRICE_PER_HOUR,
)

# How long a pod stays out of the list in the lag test.
LIST_LAG = timedelta(days=1)


@pytest.fixture
def ready(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest held."""
    set_up_cloud(runs_world)
    seed_uploaded(runs_world, held=True)
    return runs_world


def test_refused_with_an_active_run(ready: RunsWorld) -> None:
    """A local run that is not final refuses the launch; nothing is published or requested."""
    fingerprint = plan_fingerprint(ready)
    seed_run(ready, "queued")
    refused = send(ready, LAUNCH, confirm_fingerprint=fingerprint)
    assert (error_code(refused), posts(ready), batch_state(ready)[1]) == (ERROR_RUN_ACTIVE, 0, True)


def test_refused_with_a_straggler(ready: RunsWorld) -> None:
    """A live app pod that no local run knows (an exited one too) refuses the launch."""
    ready.cloud.api.pods.add(app_pod_name(RUN_ID, ready.now()), status="EXITED")
    code, details = error_of(launch_confirmed(ready))
    assert (code, details["run_ids"]) == (ERROR_RUN_ACTIVE, [RUN_ID])


def test_deployed_queued_pod_with_unlisted_pod_refuses(ready: RunsWorld) -> None:
    """A `DEPLOYED` queued pod whose pod the list does not show yet refuses the launch (list lag)."""
    api = ready.cloud.api
    name = app_pod_name(RUN_ID, ready.now())
    api.settings.list_lag = LIST_LAG
    pod = api.pods.add(name)
    api.settings.list_lag = timedelta(0)
    entry = api.queued_pods.add(name, status=QUEUED_POD_DEPLOYED)
    entry["podId"] = pod["id"]
    assert error_code(launch_confirmed(ready)) == ERROR_RUN_ACTIVE


def test_terminal_entries_do_not_refuse(ready: RunsWorld) -> None:
    """Terminated pods and failed, expired or cancelled queued pods are kept in the lists but refuse nothing."""
    api = ready.cloud.api
    name = app_pod_name(RUN_ID, ready.now())
    api.pods.add(name, status=POD_TERMINATED)
    for status in (QUEUED_POD_FAILED, QUEUED_POD_EXPIRED, QUEUED_POD_CANCELLED):
        api.queued_pods.add(name, status=status)
    seed_run(ready, "finished")
    assert launch_run(ready).status == "running"


def test_launch_needs_the_cloud_setup(runs_world: RunsWorld) -> None:
    """Without the template and volume: `cloud_not_set_up`, nothing recorded or requested."""
    seed_uploaded(runs_world, held=True)
    refused = launch_confirmed(runs_world)
    assert (error_code(refused), run_ids(runs_world), posts(runs_world)) == (ERROR_CLOUD_NOT_SET_UP, [], 0)


def test_nothing_to_launch(runs_world: RunsWorld) -> None:
    """No pending manifest: `nothing_to_launch`, before any confirmation."""
    set_up_cloud(runs_world)
    assert error_code(send(runs_world, LAUNCH)) == ERROR_NOTHING_TO_LAUNCH


@pytest.mark.parametrize(
    "status",
    [httpx.codes.PAYMENT_REQUIRED, httpx.codes.BAD_REQUEST, httpx.codes.CONFLICT],
)
def test_refused_pod_request_is_launch_failed(ready: RunsWorld, status: int) -> None:
    """Runpod refusing the POST created nothing: the run is `launch_failed`, the next launch works."""
    ready.cloud.api.fail(POST, PODS, Failure(status=status))
    code, details = error_of(launch_confirmed(ready))
    assert (code, details["http_status"]) == (ERROR_LAUNCH_REFUSED, status)
    assert (run_status(ready, run_ids(ready)[0]), posts(ready)) == ("launch_failed", 1)
    assert launch_run(ready).status == "running"


def test_unreachable_activity_check_refuses(ready: RunsWorld) -> None:
    """The activity check failing closed refuses the launch (`cloud_unreachable`)."""
    fingerprint = plan_fingerprint(ready)
    ready.cloud.api.fail("GET", PODS, *OUTAGE)
    code, details = error_of(send(ready, LAUNCH, confirm_fingerprint=fingerprint))
    assert (code, details["part"], run_ids(ready)) == (
        ERROR_CLOUD_UNREACHABLE,
        "runpod",
        [],
    )


def test_catalog_failure_uses_the_fallback_price(ready: RunsWorld) -> None:
    """An unreadable price list does not fail the plan: the usual price, with a note."""
    ready.cloud.api.fail("GET", CATALOG, *OUTAGE)
    cost = plan_of(ready).cost
    assert (cost.price_source, cost.price_per_hour_usd, cost.note is not None) == (
        "fallback",
        FALLBACK_PRICE_PER_HOUR,
        True,
    )
