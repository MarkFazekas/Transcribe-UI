"""Storing results: backup first, then R2 (subtitle, then the JSON "done" marker); batch summary."""

import logging
from collections.abc import Callable
from datetime import datetime

from transcribe_contracts import layout
from transcribe_contracts.classes import BatchSummary, JobCounts
from transcribe_contracts.constants import JSON_SUFFIX
from transcribe_contracts.errors import ContractError
from transcribe_contracts.reading import read_job_result
from transcribe_worker.pipeline.backup import Backup
from transcribe_worker.pipeline.constants import JSON_CONTENT_TYPE, SRT_CONTENT_TYPE
from transcribe_worker.pipeline.plan import PlannedJob
from transcribe_worker.pipeline.process import JobOutput
from transcribe_worker.pipeline.storage import ObjectStore

logger = logging.getLogger(__name__)


def store_output(store: ObjectStore, backup: Backup, planned: PlannedJob, output: JobOutput) -> bool:
    """Back up and upload a job's result; return True once its JSON marker is in R2."""
    body = output.job_result.model_dump_json().encode()
    backup.save(planned.srt_key, output.srt, marker=False)
    backup.save(planned.json_key, body, marker=True)
    return _upload(store, planned, output.srt, body)


def restore_from_backup(store: ObjectStore, backup: Backup, planned: PlannedJob) -> bool:
    """Upload a result an earlier run backed up but could not upload; True once it is in R2."""
    srt = backup.load(planned.srt_key)
    body = backup.load(planned.json_key)
    if srt is None or body is None:
        logger.error("backup of %s disappeared", planned.json_key)
        return False
    logger.info("uploading %s from the backup", planned.json_key)
    return _upload(store, planned, srt, body)


def finish_batch(store: ObjectStore, batch_id: str, run_id: str, now: Callable[[], datetime]) -> None:
    """Write `_done.json` with the counts of every result of the batch (all runs).

    Storage errors are only logged: the batch stays open and the next run finishes it.
    """
    try:
        counts = _write_summary(store, batch_id, run_id, now)
    except Exception:
        logger.exception("could not write the summary of %s", batch_id)
        return
    logger.info("batch %s done: %s", batch_id, counts)


def _write_summary(
    store: ObjectStore,
    batch_id: str,
    run_id: str,
    now: Callable[[], datetime],
) -> dict[str, int]:
    counts = {"ok": 0, "no_speech": 0, "failed": 0}
    for key in store.list_keys(layout.results_prefix(batch_id)):
        status = _result_status(store, key)
        if status is not None:
            counts[status] += 1
    job_counts = JobCounts(**counts)
    summary = BatchSummary(batch_id=batch_id, run_id=run_id, finished_at=now(), counts=job_counts)
    body = summary.model_dump_json().encode()
    store.write(layout.done_key(batch_id), body, JSON_CONTENT_TYPE)
    return counts


def _upload(store: ObjectStore, planned: PlannedJob, srt: bytes, body: bytes) -> bool:
    """Upload the subtitle, then the marker; an existing result (another worker) is kept."""
    try:
        created = _upload_unless_done(store, planned, srt, body)
    except Exception:
        logger.exception("could not upload %s", planned.json_key)
        return False
    if not created:
        logger.warning("%s exists already (another worker?); kept the existing result", planned.json_key)
    return True


def _upload_unless_done(store: ObjectStore, planned: PlannedJob, srt: bytes, body: bytes) -> bool:
    if store.read(planned.json_key) is not None:
        return False
    store.write(planned.srt_key, srt, SRT_CONTENT_TYPE)
    return store.write_new(planned.json_key, body, JSON_CONTENT_TYPE)


def _result_status(store: ObjectStore, key: str) -> str | None:
    if not key.endswith(JSON_SUFFIX):
        return None
    body = store.read(key)
    if body is None:
        return None
    try:
        return read_job_result(key, body).status
    except ContractError:
        logger.exception("unreadable result %s", key)
        return None
