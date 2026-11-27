"""The pod a launch requests: its name with the deadline, and the `POST /pods` body (plan m5 §1 "Pod create body")."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NamedTuple

from transcribe_contracts.pods import parse_pod_name, pod_name
from transcribe_ui.runpod.bodies import CreatePod, GlobalMount, GpuRequest, Mounts, QueueRequest
from transcribe_ui.runpod.inventory import CloudSetup
from transcribe_ui.runs import constants as launch_settings
from transcribe_ui.runs.classes import LaunchPlan, ScopeBatch
from transcribe_ui.runs.payloads import RunPayload
from transcribe_ui.upload.classes import HeldPublication


@dataclass(frozen=True)
class Decided:
    """What a launch was decided on: the plan, the cloud setup and the held manifests just published."""

    plan: LaunchPlan
    setup: CloudSetup
    published: HeldPublication

    def covered(self) -> tuple[str, ...]:
        """Return the batches the run covers: the scope, less held ones that stay held (busy)."""
        still_held = {*self.published.busy, *self.published.incomplete}
        scope = self.plan.scope.batches
        return tuple(batch.batch_id for batch in scope if _covered(batch, still_held))


def _covered(batch: ScopeBatch, still_held: set[str]) -> bool:
    return not batch.held or batch.batch_id not in still_held


class NamedRun(NamedTuple):
    """A run's pod name, its queue's `expiresAt` and the deadline in the name."""

    pod_name: str
    expires_at: datetime
    deadline: datetime


@dataclass(frozen=True)
class NewRun:
    """A run about to be recorded: its id, pod name and payload."""

    run_id: str
    pod_name: str
    payload: RunPayload


def name_run(run_id: str, now: datetime, wait_s: int, max_runtime_s: int) -> NamedRun:
    """Return the pod name, the queue's `expiresAt` and the name deadline (rounded up to the minute).

    The deadline is the queue's `expiresAt` + `MAX_RUNTIME_S` + a margin: a queued pod may
    deploy just before it expires.
    """
    expires_at = now + timedelta(seconds=wait_s)
    deadline = expires_at + timedelta(seconds=max_runtime_s + launch_settings.NAME_DEADLINE_MARGIN_S)
    name = pod_name(run_id, deadline)
    parsed = parse_pod_name(name)
    # The app's own names always parse; the parsed deadline is the one rounded up to the minute.
    return NamedRun(name, expires_at, deadline if parsed is None else parsed.deadline)


def create_body(run: NewRun, setup: CloudSetup) -> CreatePod:
    """Return the `POST /pods` body: the template (which pins the image), GPU, disk, volume, env and queue."""
    payload = run.payload
    keep_alive = launch_settings.ENV_TRUE if payload.test_mode else launch_settings.ENV_FALSE
    env = {
        launch_settings.ENV_RUN_ID: run.run_id,
        launch_settings.ENV_MAX_RUNTIME_S: str(payload.max_runtime_s),
        launch_settings.ENV_KEEP_POD_ALIVE: keep_alive,
    }
    gpu = GpuRequest(
        gpu_id=launch_settings.GPU_TYPE_ID,
        count=launch_settings.GPU_COUNT,
        min_cuda_version=launch_settings.MIN_CUDA_VERSION,
    )
    mount = GlobalMount(volume_id=setup.global_volume_id, path=launch_settings.VOLUME_MOUNT_PATH)
    return CreatePod(
        name=run.pod_name,
        template_id=setup.template_id,
        gpu=gpu,
        cloud=launch_settings.POD_CLOUD,
        disk=launch_settings.POD_DISK_GB,
        mounts=Mounts(global_=(mount,)),
        env=env,
        queue=QueueRequest(expires_at=payload.queue_expires_at),
    )
