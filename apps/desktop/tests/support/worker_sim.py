"""The simulated worker of the end-to-end flow (architecture §7, plan m5 M5.14).

Between two `refresh_status` requests the worker's objects are written into each parity leg's
bucket as the worker writes them: its heartbeat while it transcribes; then per job the subtitle
and the result JSON, `_done.json`, the final heartbeat and the log, after which its pod is gone.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from support.monitor import ENGINE, SUBTITLE, write_done, write_log, write_status
from support.parity import Exchange
from support.runpod import Cloud
from support.runs import JSON_TYPE
from transcribe_contracts.classes import JobRef, JobResult, TranscriptionMode
from transcribe_contracts.classes import JobStatus as ResultStatus
from transcribe_contracts.layout import result_json_key, result_srt_key
from transcribe_ui.storage.r2 import R2ObjectStore

# Seconds the simulated worker transcribes before the next refresh.
TRANSCRIBING_S = 1200
# Audio duration every simulated result reports.
RESULT_AUDIO_S = 60


@dataclass(frozen=True)
class SimulatedJob:
    """One job the simulated worker finishes, and how."""

    item_id: str
    language: str
    status: ResultStatus
    mode: TranscriptionMode = "normal"


def write_job_result(store: R2ObjectStore, batch_id: str, job: SimulatedJob, run_id: str, now: datetime) -> None:
    """Write a job's subtitle (empty unless `ok`), then its result JSON (the worker's order)."""
    succeeded = job.status == "ok"
    store.write(
        result_srt_key(batch_id, job.item_id, job.language), SUBTITLE if succeeded else b"", "application/x-subrip"
    )
    job_result = JobResult(
        batch_id=batch_id,
        item_id=job.item_id,
        language=job.language,
        mode=job.mode,
        run_id=run_id,
        status=job.status,
        error="decoding failed" if job.status == "failed" else None,
        aligned=succeeded,
        cue_count=1 if succeeded else 0,
        audio_duration_s=RESULT_AUDIO_S,
        started_at=now - timedelta(minutes=1),
        finished_at=now,
        engine=ENGINE,
    )
    key = result_json_key(batch_id, job.item_id, job.language)
    store.write(key, job_result.model_dump_json().encode(), JSON_TYPE)


def launched_run_id(exchange: Exchange) -> str:
    """Return the run id of a `launch_worker` exchange."""
    run_id = json.loads(exchange.response)["run"]["run_id"]
    assert isinstance(run_id, str)
    return run_id


def transcribing(cloud: Cloud, store: R2ObjectStore, run_id: str, current: JobRef) -> None:
    """Write the worker's heartbeat on its first job, then let the time pass."""
    write_status(store, run_id, "transcribing", cloud.clock.now(), current=current)
    cloud.clock.advance(timedelta(seconds=TRANSCRIBING_S))


def finished(cloud: Cloud, store: R2ObjectStore, run_id: str, batch_id: str, jobs: tuple[SimulatedJob, ...]) -> None:
    """Write every job's result, `_done.json`, the final heartbeat and the log; the pod ends itself.

    Every pod and queued pod record goes, listed yet or not (list lag), so nothing of the run lives on.
    """
    now = cloud.clock.now()
    for job in jobs:
        write_job_result(store, batch_id, job, run_id, now)
    write_done(store, batch_id, run_id, now)
    write_status(store, run_id, "finished", now, done_jobs=len(jobs), total_jobs=len(jobs))
    write_log(store, run_id)
    cloud.api.pods.records.clear()
    cloud.api.queued_pods.records.clear()
    cloud.clock.advance(timedelta(minutes=1))
