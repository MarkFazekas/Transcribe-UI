"""The run: plan, start the engine, process every pending job in order, finish the batches."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
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

    def finish_batch(self, batch_id: str) -> None:
        finish_batch(self.ports.store, batch_id, self.config.run_id, self.ports.now)

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

    Failed jobs do not fail the run; only a run that cannot work at all (storage unreachable,
    engine not starting) returns EXIT_FAILED.
    """
    status = start_status(ports.store, config, ports.now)
    backup = Backup(config.backup_dir)
    run = _Run(config=config, ports=ports, backup=backup, status=status)
    error: str | None = None
    try:
        _process_plan(run, plan_run(ports.store, backup))
    except Exception as failure:
        logger.exception("the run failed")
        error = describe(failure)
    finally:
        ports.engine.close()
        status.finish(error)
    return EXIT_FAILED if error else EXIT_OK


def _process_plan(run: _Run, plan: RunPlan) -> None:
    run.status.transcribing(len(plan.jobs))
    remaining = plan.pending_per_batch()
    for batch_id, count in remaining.items():
        if not count:
            run.finish_batch(batch_id)
    to_transcribe = [planned for planned in plan.jobs if planned.source == "transcribe"]
    if to_transcribe:
        run.ports.engine.start()
    with run.prefetcher(to_transcribe) as prefetcher:
        for planned in plan.jobs:
            run.status.job_started(planned.ref)
            stored = _process_job(run, planned, prefetcher)
            run.status.job_finished()
            if stored:
                remaining[planned.batch_id] -= 1
            if stored and not remaining[planned.batch_id]:
                run.finish_batch(planned.batch_id)


def _process_job(run: _Run, planned: PlannedJob, prefetcher: Prefetcher) -> bool:
    if planned.source == "backup":
        return restore_from_backup(run.ports.store, run.backup, planned)
    prepared = prefetcher.take()
    if prepared.planned != planned:
        prepared.discard()
        msg = f"prefetch order broken: got {prepared.planned.json_key}, expected {planned.json_key}"
        raise RuntimeError(msg)
    output = transcribe_job(run.job_context(), planned, prepared.wav, prepared.problem)
    prepared.discard()
    return store_output(run.ports.store, run.backup, planned, output)
