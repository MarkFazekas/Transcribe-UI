"""Flow tests of `setup_cloud` and `cloud_setup_status` (architecture §3.7, §5.4; plan m5 M5.6).

Requests run through the backend and the CLI (parity) against a fresh fake Runpod API per leg;
golden files are regenerated with `UPDATE_SNAPSHOTS=1 uv run pytest apps/desktop/tests`. No test
talks to the real Runpod API.
"""

import json
import logging
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from support.golden import assert_golden
from support.parity import Exchange, run_one, run_parity
from support.runpod import FULL_CREDENTIALS, R2_BUCKET, R2_PRIVATE, SECRET_VALUES, Cloud, CloudLegs
from support.runpod_api import FAKE_API_KEY, Failure, FakeRunpodApi
from transcribe_ui.api.classes import ErrorResponse, Response
from transcribe_ui.api.errors import DomainError
from transcribe_ui.api.ports import CancelFlag, DiscardProgress, RequestContext
from transcribe_ui.cli.constants import EXIT_ERROR, EXIT_OK
from transcribe_ui.runpod.access import StaticCredentials
from transcribe_ui.runpod.check import check_runpod_key
from transcribe_ui.runpod.classes import SetupCloudRequest, SetupCloudResponse
from transcribe_ui.runpod.constants import (
    ERROR_CLOUD_OBJECT_IN_USE,
    ERROR_CLOUD_SETUP_INTERRUPTED,
    ERROR_CLOUD_VOLUME_DUPLICATED,
    ERROR_CREDENTIALS_MISSING,
    ERROR_RUNPOD_KEY_REJECTED,
    GLOBAL_VOLUME_NAME,
    MAX_ATTEMPTS,
    SECRET_FOR_ENV,
    TEMPLATE_NAME,
    WORKER_IMAGE,
)
from transcribe_ui.runpod.inventory import desired_template, require_cloud_setup
from transcribe_ui.runpod.setup import setup_cloud

SETUP = '{"action": "setup_cloud"}'
STATUS = '{"action": "cloud_setup_status"}'
SETUP_REQUEST = SetupCloudRequest(action="setup_cloud")
POST = "POST"
TEMPLATES = "/v2/templates"
VOLUMES = "/v2/global-volumes"
SECRETS = "/v2/account/secrets"
OLD_IMAGE = "markfazekasgh/transcribe-ui-worker:schema-0"
CONFLICT = 409
SERVICE_UNAVAILABLE = 503
LIST_LAG = timedelta(minutes=0.5)
SETTLE = timedelta(minutes=1)
# Worker env variable of the R2 secret access key (the last of the secrets).
SECRET_KEY_ENV = SECRET_FOR_ENV[-1][0]
# Ids the fake gives the first, second and third object of a collection (not monotonic).
FIRST_ID, SECOND_ID, THIRD_ID, FOURTH_ID = "7919", "5838", "3757", "1676"
# Id of the first secret the setup creates.
FIRST_SECRET = f"sec{FIRST_ID}"
# A create whose response was lost and the re-run during list lag.
TWO_OBJECTS = 2


def _response(exchange: Exchange) -> dict[str, Any]:
    document = json.loads(exchange.response)
    assert isinstance(document, dict)
    return document


def _cloud_objects(exchange: Exchange) -> list[dict[str, Any]]:
    found = _response(exchange)["cloud_objects"]
    assert isinstance(found, list)
    return found


def _outcomes(exchange: Exchange) -> list[str]:
    return [entry["outcome"] for entry in _cloud_objects(exchange)]


def _ids(exchange: Exchange) -> dict[str, str]:
    return {entry["kind"]: entry["object_id"] for entry in _cloud_objects(exchange)}


def _states(exchange: Exchange) -> dict[str, str]:
    return {entry["name"]: entry["state"] for entry in _cloud_objects(exchange)}


def _error(exchange: Exchange) -> dict[str, Any]:
    error = _response(exchange)["error"]
    assert isinstance(error, dict)
    return error


def _error_code(response: Response) -> str:
    assert isinstance(response, ErrorResponse)
    return response.error.code


def _seed_outdated(api: FakeRunpodApi) -> None:
    api.volumes.add(GLOBAL_VOLUME_NAME)
    for _, secret in SECRET_FOR_ENV:
        api.secrets.add(secret, "stale value")
    api.templates.add(TEMPLATE_NAME, OLD_IMAGE, {"R2_BUCKET": "{{ RUNPOD_SECRET_old }}"})


def _seed_duplicate_templates(api: FakeRunpodApi) -> None:
    api.volumes.add(GLOBAL_VOLUME_NAME)
    api.volumes.add("someone-elses-volume")
    for _ in range(3):
        api.templates.add(TEMPLATE_NAME, OLD_IMAGE, {})
    # Another account template of an unexpected shape: never validated, so it cannot fail setup.
    api.templates.insert({"name": "someone-elses-template", "env": "not an object"})


def _seed_duplicate_volumes(api: FakeRunpodApi) -> None:
    api.volumes.add(GLOBAL_VOLUME_NAME)
    api.volumes.add(GLOBAL_VOLUME_NAME)


def _seed_leaky_pod(api: FakeRunpodApi) -> None:
    api.pods.add("transcribe-ui-other", env={SECRET_KEY_ENV: R2_PRIVATE, "RUNPOD_API_KEY": SECRET_VALUES[-1]})
    api.fail("PATCH", f"{SECRETS}/{FIRST_SECRET}", Failure(status=SERVICE_UNAVAILABLE))


def test_setup_creates_everything_once(tmp_path: Path) -> None:
    """A fresh account gets the volume, the secrets and the template; a second run creates nothing."""
    legs = CloudLegs()
    first, second, status = run_parity(tmp_path, [SETUP, SETUP, STATUS], ports=legs)
    assert (first.exit_code, second.exit_code, _response(status)["ready"]) == (EXIT_OK, EXIT_OK, True)
    assert set(_outcomes(first)) == {"created"}
    assert _ids(second) == _ids(first)
    assert _outcomes(second) == ["unchanged", "updated", "updated", "updated", "updated", "unchanged"]
    assert_golden("setup_cloud_fresh.json", first.golden())
    assert_golden("setup_cloud_again.json", second.golden())
    assert_golden("cloud_setup_status_ready.json", status.golden())


def test_setup_writes_the_template_and_secrets(tmp_path: Path) -> None:
    """The secrets hold the R2 credentials; the template only references them."""
    legs = CloudLegs()
    run_one(tmp_path, SETUP, ports=legs)
    api = legs.clouds[-1].api
    assert api.secrets.by_name("transcribe_ui_r2_secret_access_key") == [R2_PRIVATE]
    template = next(iter(api.templates.records.values())).body
    template_env = template["env"]
    assert (template["image"], template["startSsh"], template["startJupyter"]) == (WORKER_IMAGE, False, False)
    assert template_env[SECRET_KEY_ENV] == "{{ RUNPOD_SECRET_transcribe_ui_r2_secret_access_key }}"
    assert (len(api.volumes.records), len(api.templates.records)) == (1, 1)


def test_status_of_an_empty_account(tmp_path: Path) -> None:
    """Nothing set up: every object is missing and `launch_worker` could not run."""
    exchange = run_one(tmp_path, STATUS, ports=CloudLegs())
    assert exchange.exit_code == EXIT_OK
    assert set(_states(exchange).values()) == {"missing"}
    assert _response(exchange)["ready"] is False
    assert_golden("cloud_setup_status_missing.json", exchange.golden())


def test_outdated_template_is_reported_then_updated(tmp_path: Path) -> None:
    """An old image tag, SSH on and stale env are reported by name and fixed by the setup."""
    legs = CloudLegs(seed=_seed_outdated)
    before, setup, after = run_parity(tmp_path, [STATUS, SETUP, STATUS], ports=legs)
    template = _cloud_objects(before)[-1]
    assert (template["state"], template["outdated_fields"]) == (
        "outdated",
        ["image", "env", "start_ssh", "start_jupyter"],
    )
    assert (_outcomes(setup)[-1], _response(after)["ready"]) == ("updated", True)
    api = legs.clouds[-1].api
    assert api.secrets.by_name("transcribe_ui_r2_bucket") == [R2_BUCKET]
    assert_golden("cloud_setup_status_outdated.json", before.golden())
    assert_golden("setup_cloud_outdated.json", setup.golden())


def test_duplicate_templates_are_reduced(tmp_path: Path) -> None:
    """Several templates with the app's name: reported, then all but a stable pick deleted."""
    legs = CloudLegs(seed=_seed_duplicate_templates)
    before, setup = run_parity(tmp_path, [STATUS, SETUP], ports=legs)
    assert _states(before)[TEMPLATE_NAME] == "duplicated"
    # All three are outdated alike, so the lowest id is kept: the third created, not the first.
    assert _ids(setup)["template"] == f"tpl{THIRD_ID}"
    api = legs.clouds[-1].api
    # The kept template and the unrelated one.
    assert sorted(api.templates.records) == [f"tpl{FOURTH_ID}", f"tpl{THIRD_ID}"]
    assert_golden("setup_cloud_duplicates.json", setup.golden())


def test_up_to_date_template_is_kept(cloud: Cloud) -> None:
    """Of duplicate templates, one already up to date is kept, whatever its id."""
    cloud.api.templates.add(TEMPLATE_NAME, OLD_IMAGE, {})
    current = cloud.api.templates.insert(desired_template().body())
    cloud.api.templates.add(TEMPLATE_NAME, OLD_IMAGE, {})
    response = cloud.set_up()
    assert isinstance(response, SetupCloudResponse)
    template = response.cloud_objects[-1]
    assert (template.object_id, template.outcome, template.duplicates_removed) == (current["id"], "unchanged", 2)


def test_duplicate_volumes_are_never_deleted(tmp_path: Path) -> None:
    """Several Global Volumes may hold backups: setup and the launch check stop and list them."""
    legs = CloudLegs(seed=_seed_duplicate_volumes)
    before, setup = run_parity(tmp_path, [STATUS, SETUP], ports=legs)
    assert (_states(before)[GLOBAL_VOLUME_NAME], _response(before)["ready"]) == ("duplicated", False)
    assert _error(setup)["code"] == ERROR_CLOUD_VOLUME_DUPLICATED
    assert _error(setup)["details"] == {"volume_ids": [f"gsb_{SECOND_ID}", f"gsb_{FIRST_ID}"]}
    api = legs.clouds[-1].api
    assert (len(api.volumes.records), api.count("DELETE", f"{VOLUMES}/gsb_{FIRST_ID}")) == (TWO_OBJECTS, 0)
    with legs.clouds[-1].client() as client, pytest.raises(DomainError, match="Global Volumes"):
        require_cloud_setup(client)
    assert_golden("setup_cloud_volume_duplicated.json", setup.golden())


def test_lost_create_is_adopted_by_the_next_run(cloud: Cloud) -> None:
    """A create whose response is lost is not sent again; a later run adopts the object."""
    cloud.settings.list_lag = LIST_LAG
    cloud.api.fail(POST, TEMPLATES, Failure(lost_response=True))
    response = cloud.set_up()
    assert isinstance(response, ErrorResponse)
    assert (response.error.code, response.error.details) == (
        ERROR_CLOUD_SETUP_INTERRUPTED,
        {"kind": "template", "name": TEMPLATE_NAME},
    )
    cloud.clock.advance(SETTLE)
    again = cloud.set_up()
    assert isinstance(again, SetupCloudResponse)
    assert again.cloud_objects[-1].outcome == "unchanged"
    assert (cloud.api.count(POST, TEMPLATES), len(cloud.api.templates.records)) == (1, 1)


def test_rerun_during_list_lag_is_cleaned_up_later(cloud: Cloud) -> None:
    """Re-run before the lost template is listed: a second one is created, and removed later."""
    cloud.api.fail(POST, TEMPLATES, Failure(status=SERVICE_UNAVAILABLE))
    # The first run sets up everything but the template (a 503 on a create: never re-sent).
    first = _error_code(cloud.set_up())
    cloud.settings.list_lag = LIST_LAG
    cloud.api.fail(POST, TEMPLATES, Failure(lost_response=True))
    lost = _error_code(cloud.set_up())
    during_lag = cloud.set_up()
    created = len(cloud.api.templates.records)
    cloud.clock.advance(SETTLE)
    final = cloud.set_up()
    assert (first, lost, isinstance(during_lag, SetupCloudResponse), created) == (
        ERROR_CLOUD_SETUP_INTERRUPTED,
        ERROR_CLOUD_SETUP_INTERRUPTED,
        True,
        TWO_OBJECTS,
    )
    assert isinstance(final, SetupCloudResponse)
    template = final.cloud_objects[-1]
    assert (template.object_id, template.duplicates_removed) == (f"tpl{SECOND_ID}", 1)
    assert list(cloud.api.templates.records) == [f"tpl{SECOND_ID}"]


def test_lost_volume_create_in_list_lag_is_kept(cloud: Cloud) -> None:
    """The volume case of the same race ends with two volumes the user resolves (never deleted)."""
    cloud.settings.list_lag = LIST_LAG
    cloud.api.fail(POST, VOLUMES, Failure(lost_response=True))
    cloud.set_up()
    cloud.set_up()
    cloud.clock.advance(SETTLE)
    assert _error_code(cloud.set_up()) == ERROR_CLOUD_VOLUME_DUPLICATED
    assert len(cloud.api.volumes.records) == TWO_OBJECTS


def test_create_with_unreadable_answer_is_adopted(cloud: Cloud) -> None:
    """A 201 whose body does not parse still created the object: not sent again, adopted later."""
    cloud.api.fail(POST, TEMPLATES, Failure(garbled_body=True))
    assert _error_code(cloud.set_up()) == ERROR_CLOUD_SETUP_INTERRUPTED
    again = cloud.set_up()
    assert isinstance(again, SetupCloudResponse)
    assert (again.cloud_objects[-1].outcome, cloud.api.count(POST, TEMPLATES)) == ("unchanged", 1)


def test_create_that_never_reached_runpod_is_retried(cloud: Cloud) -> None:
    """A failed connection sent nothing, so the create is sent again within the same request."""
    cloud.api.fail(POST, VOLUMES, Failure(connect_error=True))
    assert isinstance(cloud.set_up(), SetupCloudResponse)
    assert (cloud.api.count(POST, VOLUMES), len(cloud.api.volumes.records)) == (TWO_OBJECTS, 1)


def test_secret_taken_but_not_listed_yet(cloud: Cloud) -> None:
    """A 409 on a secret create (it exists but the list lags) asks for a re-run."""
    cloud.api.fail(POST, SECRETS, Failure(status=CONFLICT))
    assert _error_code(cloud.set_up()) == ERROR_CLOUD_SETUP_INTERRUPTED


def test_duplicate_in_use_is_not_removed(cloud: Cloud) -> None:
    """A duplicate template a live pod was created from stays; the request explains what to do."""
    cloud.api.volumes.add(GLOBAL_VOLUME_NAME)
    in_use = cloud.api.templates.add(TEMPLATE_NAME, OLD_IMAGE, {})
    cloud.api.templates.add(TEMPLATE_NAME, OLD_IMAGE, {})
    pod = cloud.api.pods.add("someone-elses-pod")
    pod["template"] = in_use["id"]
    response = cloud.set_up()
    assert isinstance(response, ErrorResponse)
    assert (response.error.code, response.error.details["object_id"]) == (ERROR_CLOUD_OBJECT_IN_USE, in_use["id"])


def test_exact_secret_name_wins_over_a_case_variant(cloud: Cloud) -> None:
    """Runpod matches secret names case-insensitively; the exact name is kept, the variant removed."""
    name = SECRET_FOR_ENV[1][1]
    variant = cloud.api.secrets.add(name.upper(), "old")
    exact = cloud.api.secrets.add(name, "old")
    response = cloud.set_up()
    assert isinstance(response, SetupCloudResponse)
    assert (response.cloud_objects[2].object_id, response.cloud_objects[2].duplicates_removed) == (exact["id"], 1)
    assert variant["id"] not in cloud.api.secrets.records


def test_missing_credentials_are_named(tmp_path: Path) -> None:
    """Without credentials nothing is sent; the error names what is missing (never values)."""
    legs = CloudLegs(credentials=StaticCredentials())
    setup, status = run_parity(tmp_path, [SETUP, STATUS], ports=legs)
    assert (setup.exit_code, _error(setup)["code"], _error(status)["code"]) == (
        EXIT_ERROR,
        ERROR_CREDENTIALS_MISSING,
        ERROR_CREDENTIALS_MISSING,
    )
    assert not legs.clouds[0].api.seen()
    assert_golden("setup_cloud_credentials_missing.json", setup.golden())


def test_missing_r2_credentials_alone(tmp_path: Path) -> None:
    """With only the Runpod key stored, the error names the R2 credentials."""
    legs = CloudLegs(credentials=StaticCredentials(api_key=FULL_CREDENTIALS.api_key))
    assert _error(run_one(tmp_path, SETUP, ports=legs))["details"] == {"missing": ["r2"]}


def test_rejected_key(tmp_path: Path) -> None:
    """A key Runpod refuses gives `runpod_key_rejected`, not retried."""
    legs = CloudLegs(credentials=StaticCredentials(api_key=SecretStr("rpa_WRONG"), r2=FULL_CREDENTIALS.r2))
    exchange = run_one(tmp_path, SETUP, ports=legs)
    assert _error(exchange)["code"] == ERROR_RUNPOD_KEY_REJECTED
    assert len(legs.clouds[0].api.seen()) == 1
    assert_golden("setup_cloud_key_rejected.json", exchange.golden())


def test_cancel_stops_before_creating(cloud: Cloud) -> None:
    """A cancelled setup stops between steps; running it again resumes."""
    cancel = CancelFlag()
    cancel.cancel()
    assert _error_code(cloud.set_up(cancel)) == "cancelled"
    assert not cloud.api.volumes.records


def test_cancel_ends_a_retry_wait(cloud: Cloud) -> None:
    """A cancel during a wait between attempts stops the request without another attempt."""
    cancel = CancelFlag()
    cloud.sleeps.on_wait = cancel.cancel
    cloud.api.fail("GET", VOLUMES, Failure(status=SERVICE_UNAVAILABLE))
    assert _error_code(cloud.set_up(cancel)) == "cancelled"
    assert (cloud.api.count("GET", VOLUMES), cloud.sleeps.delays) == (1, [1.0])


def test_launch_check(cloud: Cloud) -> None:
    """`launch_worker`'s check: not set up, outdated or duplicated refused; ready gives the ids."""
    with cloud.client() as client, pytest.raises(DomainError, match="incomplete"):
        require_cloud_setup(client)
    cloud.set_up()
    with cloud.client() as client:
        ready = require_cloud_setup(client)
    assert (ready.template_id, ready.global_volume_id) == (f"tpl{FIRST_ID}", f"gsb_{FIRST_ID}")
    cloud.api.templates.add(TEMPLATE_NAME, WORKER_IMAGE, {})
    with cloud.client() as client, pytest.raises(DomainError, match="outdated"):
        require_cloud_setup(client)


def test_secrets_never_leave(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Pods whose env holds the secrets, a setup that sends them: no response or log shows one."""
    caplog.set_level(logging.DEBUG)
    legs = CloudLegs(seed=_seed_leaky_pod)
    exchanges = run_parity(tmp_path, [STATUS, SETUP, SETUP, STATUS], ports=legs)
    shown = [exchange.response for exchange in exchanges]
    for leg in legs.clouds:
        checked = check_runpod_key(leg.access())
        with leg.client() as client:
            shown.append(f"{checked!r} {client.pods.list_pods()!r}")
    shown.append(caplog.text)
    assert legs.clouds[0].api.count("GET", "/v2/pods") == TWO_OBJECTS
    joined = " ".join(shown)
    assert not any(secret in joined for secret in SECRET_VALUES)


@pytest.fixture
def cloud(tmp_path: Path) -> Cloud:
    """Return a fake Runpod account whose `set_up()` runs `setup_cloud` (local state under `tmp_path`)."""
    return Cloud(root=tmp_path)


def _domain_error(call: Callable[[], object]) -> DomainError:
    try:
        call()
    except DomainError as error:
        return error
    raise AssertionError


@pytest.mark.parametrize(
    ("path", "failure"),
    [
        (VOLUMES, Failure(status=SERVICE_UNAVAILABLE)),
        (TEMPLATES, Failure(lost_response=True)),
        (SECRETS, Failure(status=CONFLICT)),
    ],
    ids=["list-failed", "ambiguous-create", "secret-conflict"],
)
def test_domain_errors_chain_nothing(tmp_path: Path, path: str, failure: Failure) -> None:
    """The errors of the setup are raised outside `except` blocks: no Runpod error is chained."""
    account = Cloud()
    method = "GET" if path == VOLUMES else POST
    account.api.fail(method, path, *(failure,) * MAX_ATTEMPTS)
    context = RequestContext(account.ports(tmp_path), DiscardProgress(), CancelFlag())
    error = _domain_error(lambda: setup_cloud(SETUP_REQUEST, context))
    assert (error.__cause__, error.__context__) == (None, None)
    assert FAKE_API_KEY not in f"{error!r} {error.details!r}"
