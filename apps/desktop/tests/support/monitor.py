"""Simulated worker objects and helpers of the monitoring tests (plan m5 M5.12).

The worker's objects are written into moto as the worker writes them: `runs/<run_id>/status.json`
(the heartbeat), `worker.log`, each job's `.srt` and result JSON, and `_done.json` after a batch's
last job. Nothing talks to a real worker or Runpod.
"""

from datetime import datetime, timedelta

from support.launch import ok_as, send
from support.runs import JSON_TYPE, RunsWorld
from transcribe_contracts.classes import BatchSummary, EngineInfo, JobCounts, JobRef, JobResult, RunState, RunStatus
from transcribe_contracts.classes import JobStatus as ResultStatus
from transcribe_contracts.layout import done_key, result_json_key, result_srt_key, run_log_key, run_status_key
from transcribe_ui.runs.responses import RefreshStatusResponse, RunProgress
from transcribe_ui.state.models import JobRow
from transcribe_ui.storage.r2 import R2ObjectStore

# Request action of the monitoring.
REFRESH = "refresh_status"
# Pod id the simulated worker reports (the worker's own `RUNPOD_POD_ID`).
WORKER_POD_ID = "pod-worker"
# Text of the simulated worker log.
WORKER_LOG = "worker started\nworker finished\n"
# Batch size of the simulated engine.
ENGINE_BATCH_SIZE = 16
# Engine settings of the simulated results.
ENGINE = EngineInfo(
    name="whisperx", version="3.4.2", model="large-v3", compute_type="float16", batch_size=ENGINE_BATCH_SIZE
)
# Subtitle of a simulated `ok` result.
SUBTITLE = b"1\n00:00:00,000 --> 00:00:01,000\nHello\n"


def write_status(  # noqa: PLR0913, WPS211 -- one keyword per heartbeat field a test sets
    store: R2ObjectStore,
    run_id: str,
    state: RunState,
    now: datetime,
    *,
    current: JobRef | None = None,
    done_jobs: int = 0,
    total_jobs: int = 2,
) -> None:
    """Write the worker's heartbeat as the worker does (its deadline two hours after its start)."""
    started = now - timedelta(minutes=1)
    status = RunStatus(
        run_id=run_id,
        pod_id=WORKER_POD_ID,
        state=state,
        started_at=started,
        updated_at=now,
        deadline_at=started + timedelta(hours=2),
        current=current,
        done_jobs=done_jobs,
        total_jobs=total_jobs,
        error="the GPU is gone" if state == "failed" else None,
    )
    store.write(run_status_key(run_id), status.model_dump_json().encode(), JSON_TYPE)


def write_log(store: R2ObjectStore, run_id: str, text: str = WORKER_LOG) -> None:
    """Upload the worker log."""
    store.write(run_log_key(run_id), text.encode(), "text/plain")


def write_result(  # noqa: PLR0913, WPS211 -- the job's key parts, its run, its time and its outcome
    store: R2ObjectStore,
    batch_id: str,
    item_id: str,
    run_id: str,
    now: datetime,
    *,
    status: ResultStatus = "ok",
) -> None:
    """Write a job's subtitle, then its result JSON (the worker's order)."""
    store.write(result_srt_key(batch_id, item_id, "en"), SUBTITLE if status == "ok" else b"", "application/x-subrip")
    job_result = JobResult(
        batch_id=batch_id,
        item_id=item_id,
        language="en",
        mode="normal",
        run_id=run_id,
        status=status,
        error="decoding failed" if status == "failed" else None,
        aligned=status == "ok",
        cue_count=1 if status == "ok" else 0,
        audio_duration_s=60,
        started_at=now - timedelta(minutes=1),
        finished_at=now,
        engine=ENGINE,
    )
    store.write(result_json_key(batch_id, item_id, "en"), job_result.model_dump_json().encode(), JSON_TYPE)


def write_done(store: R2ObjectStore, batch_id: str, run_id: str, now: datetime) -> None:
    """Write the batch summary the worker writes after the batch's last job."""
    summary = BatchSummary(
        batch_id=batch_id, run_id=run_id, finished_at=now, counts=JobCounts(ok=1, no_speech=0, failed=0)
    )
    store.write(done_key(batch_id), summary.model_dump_json().encode(), JSON_TYPE)


def refresh(world: RunsWorld) -> RefreshStatusResponse:
    """Send `refresh_status`; it must succeed."""
    return ok_as(send(world, REFRESH), RefreshStatusResponse)


def refreshed_run(world: RunsWorld, run_id: str) -> RunProgress:
    """Send `refresh_status`; return the run's progress in its response."""
    return progress_of(refresh(world), run_id)


def progress_of(response: RefreshStatusResponse, run_id: str) -> RunProgress:
    """Return the run's progress in a refresh response."""
    found = [each for each in response.runs if each.run.run_id == run_id]
    assert len(found) == 1, response
    return found[0]


def end_pod(world: RunsWorld, pod_id: str) -> None:
    """Let the pod terminate itself (the worker's entry): it leaves the lists and reads 404."""
    pods = world.cloud.api.pods
    pods.records.pop(pod_id)


def run_objects(world: RunsWorld, run_id: str) -> list[str]:
    """Return the keys under `runs/<run_id>/`."""
    return [found.key for found in world.store.list_objects(f"runs/{run_id}/")]


def job_statuses(world: RunsWorld, batch_id: str) -> dict[str, str]:
    """Return the batch's job statuses by item id."""
    with world.ports.state.session() as session:
        rows = session.query(JobRow).filter(JobRow.batch_id == batch_id)
        return {row.item_id: row.status for row in rows}
