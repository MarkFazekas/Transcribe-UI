"""Flow tests of `setup_cloud` and `cloud_setup_status` (architecture §3.7, §5.4; plan m5 M5.6).

Requests run through the backend and the CLI (parity) against a fresh fake Runpod API per leg;
golden files are regenerated with `UPDATE_SNAPSHOTS=1 uv run pytest apps/desktop/tests`. No test
talks to the real Runpod API.
"""

import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from support.golden import assert_golden
from support.parity import Exchange, run_one, run_parity
from support.runpod import FULL_CREDENTIALS, R2_BUCKET, R2_PRIVATE, SECRET_VALUES, Cloud, CloudLegs
from support.runpod_api import Failure, FakeRunpodApi, FakeSettings
from transcribe_ui.api.classes import ErrorResponse, Response
from transcribe_ui.api.errors import DomainError
from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.cli.constants import EXIT_ERROR, EXIT_OK
from transcribe_ui.runpod.access import StaticCredentials
from transcribe_ui.runpod.classes import SetupCloudRequest, SetupCloudResponse
from transcribe_ui.runpod.constants import (
    ERROR_CLOUD_OBJECT_IN_USE,
    ERROR_CLOUD_SETUP_INTERRUPTED,
    ERROR_CREDENTIALS_MISSING,
    ERROR_RUNPOD_KEY_REJECTED,
    GLOBAL_VOLUME_NAME,
    SECRET_FOR_ENV,
    TEMPLATE_NAME,
    WORKER_IMAGE,
)
from transcribe_ui.runpod.inventory import require_cloud_setup

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


def _seed_duplicates(api: FakeRunpodApi) -> None:
    for _ in range(3):
        api.volumes.add(GLOBAL_VOLUME_NAME)
        api.templates.add(TEMPLATE_NAME, OLD_IMAGE, {})
    api.volumes.add("someone-elses-volume")


def _seed_leaky_pod(api: FakeRunpodApi) -> None:
    api.pods.add("transcribe-ui-other", env={SECRET_KEY_ENV: R2_PRIVATE, "RUNPOD_API_KEY": SECRET_VALUES[-1]})
    api.fail("PATCH", f"{SECRETS}/sec0001", Failure(status=SERVICE_UNAVAILABLE))


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


def test_duplicates_are_reduced_to_the_oldest(tmp_path: Path) -> None:
    """Several objects with one fixed name: reported as duplicated, then all but the oldest deleted."""
    legs = CloudLegs(seed=_seed_duplicates)
    before, setup = run_parity(tmp_path, [STATUS, SETUP], ports=legs)
    states = _states(before)
    assert (states[GLOBAL_VOLUME_NAME], states[TEMPLATE_NAME]) == ("duplicated", "duplicated")
    kept = _ids(setup)
    assert (kept["global_volume"], kept["template"]) == ("gsb_0001", "tpl0001")
    api = legs.clouds[-1].api
    assert (sorted(api.volumes.records), list(api.templates.records)) == (["gsb_0001", "gsb_0004"], ["tpl0001"])
    assert_golden("setup_cloud_duplicates.json", setup.golden())


def test_lost_create_is_adopted_by_the_next_run(tmp_path: Path) -> None:
    """A create whose response is lost is not sent again; a later run adopts the object."""
    cloud = Cloud(settings=FakeSettings(list_lag=LIST_LAG))
    cloud.api.fail(POST, TEMPLATES, Failure(lost_response=True))
    backend = cloud.backend(tmp_path)
    response = backend.execute(SETUP_REQUEST)
    assert isinstance(response, ErrorResponse)
    assert (response.error.code, response.error.details) == (
        ERROR_CLOUD_SETUP_INTERRUPTED,
        {"kind": "template", "name": TEMPLATE_NAME},
    )
    cloud.clock.advance(SETTLE)
    again = backend.execute(SETUP_REQUEST)
    assert isinstance(again, SetupCloudResponse)
    assert again.cloud_objects[-1].outcome == "unchanged"
    assert (cloud.api.count(POST, TEMPLATES), len(cloud.api.templates.records)) == (1, 1)


def test_rerun_during_list_lag_is_cleaned_up_later(tmp_path: Path) -> None:
    """Re-run before the lost object is listed: a second one is created, and removed later."""
    cloud = Cloud(settings=FakeSettings(list_lag=LIST_LAG))
    cloud.api.fail(POST, VOLUMES, Failure(lost_response=True))
    backend = cloud.backend(tmp_path)
    assert _error_code(backend.execute(SETUP_REQUEST)) == ERROR_CLOUD_SETUP_INTERRUPTED
    assert isinstance(backend.execute(SETUP_REQUEST), SetupCloudResponse)
    assert len(cloud.api.volumes.records) == TWO_OBJECTS
    cloud.clock.advance(SETTLE)
    final = backend.execute(SETUP_REQUEST)
    assert isinstance(final, SetupCloudResponse)
    volume = final.cloud_objects[0]
    assert (volume.object_id, volume.duplicates_removed, list(cloud.api.volumes.records)) == (
        "gsb_0001",
        1,
        ["gsb_0001"],
    )


def test_create_that_never_reached_runpod_is_retried(tmp_path: Path) -> None:
    """A failed connection sent nothing, so the create is sent again within the same request."""
    cloud = Cloud()
    cloud.api.fail(POST, VOLUMES, Failure(connect_error=True))
    assert isinstance(cloud.backend(tmp_path).execute(SETUP_REQUEST), SetupCloudResponse)
    assert (cloud.api.count(POST, VOLUMES), len(cloud.api.volumes.records)) == (TWO_OBJECTS, 1)


def test_secret_taken_but_not_listed_yet(tmp_path: Path) -> None:
    """A 409 on a secret create (it exists but the list lags) asks for a re-run."""
    cloud = Cloud()
    cloud.api.fail(POST, SECRETS, Failure(status=CONFLICT))
    assert _error_code(cloud.backend(tmp_path).execute(SETUP_REQUEST)) == ERROR_CLOUD_SETUP_INTERRUPTED


def test_duplicate_in_use_is_not_removed(tmp_path: Path) -> None:
    """A duplicate volume a live pod mounts stays; the request explains what to do."""
    cloud = Cloud()
    cloud.api.volumes.add(GLOBAL_VOLUME_NAME)
    in_use = cloud.api.volumes.add(GLOBAL_VOLUME_NAME)
    pod = cloud.api.pods.add("someone-elses-pod")
    pod["mounts"] = {"global": [{"volumeId": in_use["id"]}]}
    response = cloud.backend(tmp_path).execute(SETUP_REQUEST)
    assert isinstance(response, ErrorResponse)
    assert (response.error.code, response.error.details["object_id"]) == (ERROR_CLOUD_OBJECT_IN_USE, in_use["id"])


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


def test_cancel_stops_before_creating(tmp_path: Path) -> None:
    """A cancelled setup stops between steps; running it again resumes."""
    cloud = Cloud()
    cancel = CancelFlag()
    cancel.cancel()
    assert _error_code(cloud.backend(tmp_path).execute(SETUP_REQUEST, cancel=cancel)) == "cancelled"
    assert not cloud.api.volumes.records


def test_launch_check(tmp_path: Path) -> None:
    """`launch_worker`'s check: not set up, outdated or duplicated refused; ready gives the ids."""
    cloud = Cloud()
    with cloud.client() as client, pytest.raises(DomainError, match="incomplete"):
        require_cloud_setup(client)
    cloud.backend(tmp_path).execute(SETUP_REQUEST)
    with cloud.client() as client:
        ready = require_cloud_setup(client)
    assert (ready.template_id, ready.global_volume_id) == ("tpl0001", "gsb_0001")
    cloud.api.templates.add(TEMPLATE_NAME, WORKER_IMAGE, {})
    with cloud.client() as client, pytest.raises(DomainError, match="outdated"):
        require_cloud_setup(client)


def test_secrets_never_leave(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Pods whose env holds the secrets, a setup that sends them: no response or log shows one."""
    caplog.set_level(logging.DEBUG)
    legs = CloudLegs(seed=_seed_leaky_pod)
    exchanges = run_parity(tmp_path, [STATUS, SETUP, SETUP, STATUS], ports=legs)
    shown = " ".join(exchange.response for exchange in exchanges) + caplog.text
    assert [secret for secret in SECRET_VALUES if secret in shown] == []
