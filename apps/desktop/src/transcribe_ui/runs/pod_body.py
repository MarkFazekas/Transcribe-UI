"""The pod a launch requests: its name with the deadline, and the `POST /pods` body (plan m5 §1 "Pod create body")."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NamedTuple

from transcribe_contracts.pods import parse_pod_name, pod_name
from transcribe_ui.runpod.bodies import CreatePod, GlobalMount, GpuRequest, Mounts, QueueRequest
from transcribe_ui.runpod.inventory import CloudSetup
from transcribe_ui.runs import constants as launch_settings
from transcribe_ui.runs.classes import LaunchPlan, ScopeBatch
from transcribe_ui.runs.estimate import job_runtime_s, weighted_duration
from transcribe_ui.runs.manifests import total_duration
from transcribe_ui.runs.payloads import RunPayload


@dataclass(frozen=True)
class Decided:
    """What a launch was decided on: the plan and the cloud setup."""

    plan: LaunchPlan
    setup: CloudSetup

    def covered(self) -> tuple[str, ...]:
        """Return the batches the run covers (its lease lists them): the scope's."""
        return tuple(batch.batch_id for batch in self.plan.scope.batches)

    def run_payload(self, named: "NamedRun", *, wait_s: int, test_mode: bool) -> RunPayload:
        """Return the payload of the run launched on this decision under that name."""
        plan = self.plan
        scope = plan.scope
        return RunPayload(
            max_runtime_s=plan.max_runtime_s,
            test_mode=test_mode,
            wait_s=wait_s,
            queue_expires_at=named.expires_at,
            name_deadline=named.deadline,
            covered_batch_ids=self.covered(),
            known_batch_ids=tuple(batch.batch_id for batch in self._covered_batches(known=True)),
            fingerprint=scope.fingerprint,
            pending_duration_s=scope.pending_duration_s,
            estimate=plan.cost,
            weighted_known_duration_s=self.followed_duration_s(),
            unknown_runtime_s=self.unknown_runtime_s(),
        )

    def unknown_runtime_s(self) -> float:
        """Return the launch-factor runtime of the covered batches this database does not know (another computer's)."""
        jobs = [job for batch in self._covered_batches(known=False) for job in batch.pending_jobs]
        return total_duration(job_runtime_s(job) for job in jobs)

    def followed_duration_s(self) -> float:
        """Return the weighted pending audio of the covered batches this database knows (their progress is followed)."""
        jobs = [job for batch in self._covered_batches(known=True) for job in batch.pending_jobs]
        return weighted_duration(jobs)

    def _covered_batches(self, *, known: bool) -> list[ScopeBatch]:
        """Return the covered batches this database knows, or those it does not."""
        covered = frozenset(self.covered())
        of_run = [batch for batch in self.plan.scope.batches if batch.batch_id in covered]
        return [batch for batch in of_run if batch.known == known]


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
