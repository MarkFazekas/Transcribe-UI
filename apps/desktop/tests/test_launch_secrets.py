"""No credential reaches a launch, cancel or reconcile response or a log record (plan m5 M5.11, §3.6).

The world's credentials are sentinel values; a pod whose `env` holds one is listed too.
"""

import pytest

from support.credentials import NEVER_SHOWN, TEST_SECRET
from support.launch import CANCEL, LAUNCH, PLAN, RECONCILE, launch_run, plan_fingerprint, send, set_up_cloud
from support.runs import OTHER_RUN_ID, RunsWorld, app_pod_name, seed_uploaded


@pytest.mark.usefixtures("no_credential_logs")
def test_no_secret_in_responses_or_logs(runs_world: RunsWorld) -> None:
    """Plan, launch (asked and confirmed), reconcile and cancel answer without any credential."""
    set_up_cloud(runs_world)
    seed_uploaded(runs_world, held=True)
    env = {"R2_SECRET_ACCESS_KEY": TEST_SECRET}
    pods = runs_world.cloud.api.pods
    pods.add(app_pod_name(OTHER_RUN_ID, runs_world.now()), status="EXITED", env=env)
    texts = [send(runs_world, action).model_dump_json() for action in (PLAN, LAUNCH, RECONCILE)]
    fingerprint = plan_fingerprint(runs_world)
    texts.append(send(runs_world, LAUNCH, confirm_fingerprint=fingerprint).model_dump_json())
    pods.records.clear()
    run_id = launch_run(runs_world).run_id
    texts.append(send(runs_world, CANCEL, run_id=run_id).model_dump_json())
    texts.append(send(runs_world, RECONCILE).model_dump_json())
    leaked = [secret for secret in NEVER_SHOWN for text in texts if secret in text]
    assert leaked == []
