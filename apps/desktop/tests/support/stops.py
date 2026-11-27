"""Queued runs and a Runpod that forgets a cancelled queued pod, for the stop tests."""

from collections.abc import Callable
from functools import partial

import httpx
import pytest

from support.launch import launch_run
from support.runpod_api import QueuedPods
from support.runs import RunsWorld

# The fake's delete of a queued pod.
type Delete = Callable[[str], httpx.Response]


def queued_run(world: RunsWorld, *, deploy: bool = True) -> tuple[str, str]:
    """Launch with no capacity; let the queued pod deploy at the next request; return the run and queued-pod ids."""
    world.cloud.settings.capacity = False
    run = launch_run(world)
    assert run.queued_pod_id is not None
    if deploy:
        world.cloud.api.queued_pods.schedule_deploy(run.queued_pod_id, world.now())
    return run.run_id, run.queued_pod_id


def _delete_and_forget(queued: QueuedPods, delete: Delete, object_id: str) -> httpx.Response:
    response = delete(object_id)
    queued.records.pop(object_id, None)
    return response


def forget_queued_on_cancel(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make Runpod forget a queued pod once it is cancelled: the read after the cancel is a 404."""
    queued = world.cloud.api.queued_pods
    monkeypatch.setattr(queued, "delete", partial(_delete_and_forget, queued, queued.delete))
