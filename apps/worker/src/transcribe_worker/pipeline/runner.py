"""The run: plan, start the engine, process every pending job in order, finish the batches."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from transcribe_worker.engine.host import EngineProcess, describe
from transcribe_worker.pipeline.audio import AudioCache
from transcribe_worker.pipeline.backup import Backup
from transcribe_worker.pipeline.config import WorkerConfig
from transcribe_worker.pipeline.constants import EXIT_FAILED, EXIT_OK, PREFETCH_DEPTH
from transcribe_worker.pipeline.plan import PlannedJob, RunPlan, plan_run
from transcribe_worker.pipeline.prefetch import Decoder, Prefetcher
from transcribe_worker.pipeline.process import JobContext, transcribe_job
from transcribe_worker.pipeline.results import finish_batch, restore_from_backup, store_output
from transcribe_worker.pipeline.status import StatusReporter, start_status
from transcribe_worker.pipeline.storage import ObjectStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerPorts:
    """External dependencies of a run, injected so flow tests can replace them."""

    store: ObjectStore
    engine: EngineProcess
    decode: Decoder
    now: Callable[[], datetime]


@dataclass(frozen=True)
class _Run:
    config: WorkerConfig
    ports: WorkerPorts
    backup: Backup
    status: StatusReporter
    # Jobs left without a result because of a problem outside the file; they fail the run.
    deferred: list[str] = field(default_factory=list)

    def finish_batch(self, batch_id: str) -> None:
        ports = self.ports
        if not finish_batch(ports.store, batch_id, self.config.run_id, ports.now):
            self.deferred.append(batch_id)

    def job_context(self) -> JobContext:
        ports = self.ports
        return JobContext(
            run_id=self.config.run_id,
            engine=ports.engine,
            now=ports.now,
            min_timeout_s=self.config.job_timeout_min_s,
        )

    def prefetcher(self, jobs: list[PlannedJob]) -> Prefetcher:
        work_dir = self.config.work_dir
        cache = AudioCache(self.ports.store, work_dir / "audio", self.config.audio_cache_bytes)
        return Prefetcher(jobs, cache, work_dir / "wav", self.ports.decode, PREFETCH_DEPTH)


def run_worker(config: WorkerConfig, ports: WorkerPorts) -> int:
    """Process every pending job of every ready batch; return the process exit code.

    Failed jobs do not fail the run. EXIT_FAILED means the run could not work (storage
    unreachable, engine not starting) or left jobs without a result in R2 for the next run. An interruption (KeyboardInterrupt, SystemExit)
    is recorded as a failed run and propagates.
    """
    status = start_status(ports.store, config, ports.now)
    backup = Backup(config.backup_dir)
    run = _Run(config=config, ports=ports, backup=backup, status=status)
    error: str | None = "the run was interrupted"
    try:
        _process_plan(run, plan_run(ports.store, backup))
        error = None
    except Exception as failure:
        logger.exception("the run failed")
        error = describe(failure)
    finally:
        _shut_down(ports.engine, status, error)
    return EXIT_FAILED if error else EXIT_OK


def _shut_down(engine: EngineProcess, status: StatusReporter, error: str | None) -> None:
    try:
        engine.close()
    except Exception:
        logger.exception("could not stop the engine process")
    status.finish(error)


def _process_plan(run: _Run, plan: RunPlan) -> None:
    """Finish complete batches, restore backups, then transcribe (the engine starts last)."""
    run.status.transcribing(len(plan.jobs))
    remaining = plan.pending_per_batch()
    for batch_id, count in remaining.items():
        if not count:
            run.finish_batch(batch_id)
    restored_jobs = [planned for planned in plan.jobs if planned.source == "backup"]
    for restored in restored_jobs:
        run.status.job_started(restored.ref)
        stored = restore_from_backup(run.ports.store, run.backup, restored)
        _record(run, remaining, restored, stored=stored)
    to_transcribe = [planned for planned in plan.jobs if planned.source == "transcribe"]
    if to_transcribe:
        _transcribe_all(run, remaining, to_transcribe)
    _check_nothing_left(run)


def _transcribe_all(run: _Run, remaining: dict[str, int], to_transcribe: list[PlannedJob]) -> None:
    with run.prefetcher(to_transcribe) as prefetcher:
        run.ports.engine.start()  # after the prefetcher, so downloads overlap the model load
        for planned in to_transcribe:
            run.status.job_started(planned.ref)
            stored = _transcribe(run, planned, prefetcher)
            _record(run, remaining, planned, stored=stored)


def _check_nothing_left(run: _Run) -> None:
    """Fail the run when jobs were left without a result in R2 (retried by the next run)."""
    if run.deferred:
        msg = f"{len(run.deferred)} jobs or batch summaries left for the next run (storage, disk or ffmpeg problems)"
        raise RuntimeError(msg)


def _record(run: _Run, remaining: dict[str, int], planned: PlannedJob, *, stored: bool) -> None:
    """Count a processed job; finish its batch when its last result is in R2."""
    run.status.job_finished()
    if not stored:
        run.deferred.append(planned.json_key)
        return
    remaining[planned.batch_id] -= 1
    if not remaining[planned.batch_id]:
        run.finish_batch(planned.batch_id)


def _transcribe(run: _Run, planned: PlannedJob, prefetcher: Prefetcher) -> bool:
    """Transcribe and store one job; False when its result is not in R2 (retried next run)."""
    with prefetcher.take() as prepared:
        if prepared.planned != planned:
            msg = f"prefetch order broken: got {prepared.planned.json_key}, expected {planned.json_key}"
            raise RuntimeError(msg)
        if prepared.retry_later:
            logger.warning("%s left for the next run: %s", planned.json_key, prepared.problem)
            return False
        output = transcribe_job(run.job_context(), planned, prepared.wav, prepared.problem)
    return store_output(run.ports.store, run.backup, planned, output)
