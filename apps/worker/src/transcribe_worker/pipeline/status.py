"""Run heartbeat: `runs/<run_id>/status.json` and the uploaded log (architecture §4)."""

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from transcribe_contracts import layout
from transcribe_contracts.classes import JobRef, RunState, RunStatus
from transcribe_worker.pipeline.constants import (
    HEARTBEAT_INTERVAL_S,
    JSON_CONTENT_TYPE,
    LOG_CONTENT_TYPE,
    LOG_FILE_NAME,
    THREAD_STOP_TIMEOUT_S,
)
from transcribe_worker.pipeline.storage import ObjectStore

if TYPE_CHECKING:
    from transcribe_worker.pipeline.config import WorkerConfig

logger = logging.getLogger(__name__)


class StatusReporter:
    """Keeps the run's progress and publishes it with the log, on change and every heartbeat.

    Publishing failures are logged and never stop the run.
    """

    def __init__(  # noqa: WPS211 - each argument is a distinct, injected dependency
        self,
        store: ObjectStore,
        status: RunStatus,
        log_file: Path,
        now: Callable[[], datetime],
    ) -> None:
        """Start from an initial status (state "starting")."""
        self._store = store
        self._status = status
        self._log_file = log_file
        self._now = now
        self._lock = threading.Lock()
        # Serialises whole publishes, so an older snapshot never overwrites a newer one in R2
        # (except the final status, which writes over an upload stuck for longer than 60 s).
        self._publish_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start_heartbeat(self) -> None:
        """Publish now and then every heartbeat interval from a background thread."""
        self._publish()
        self._thread = threading.Thread(target=self._beat, name="heartbeat", daemon=True)
        self._thread.start()

    def transcribing(self, total_jobs: int) -> None:
        """Mark the run as transcribing `total_jobs` jobs."""
        self._update(state="transcribing", total_jobs=total_jobs)

    def job_started(self, current: JobRef) -> None:
        """Record the job being processed."""
        self._update(current=current)

    def job_finished(self) -> None:
        """Count a finished job."""
        with self._lock:
            done_jobs = self._status.done_jobs + 1
        self._update(done_jobs=done_jobs, current=None)

    def finish(self, error: str | None) -> None:
        """Stop the heartbeat and publish the final state ("failed" with an error)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(THREAD_STOP_TIMEOUT_S)
        state: RunState = "failed" if error else "finished"
        # Bounded wait for a heartbeat upload still in flight; the R2 timeouts bound the rest.
        self._update(state=state, current=None, error=error, lock_timeout_s=THREAD_STOP_TIMEOUT_S)

    def _publish(self, lock_timeout_s: float = -1) -> None:
        """Upload the current status and the log; failures are only logged.

        Args:
            lock_timeout_s: How long to wait for an upload in flight (-1: as long as it takes).
        """
        acquired = self._publish_lock.acquire(timeout=lock_timeout_s)
        if not acquired:
            # Only the final status waits with a limit: it is written over the stuck upload.
            logger.warning("an earlier status upload is still running; publishing over it")
        try:
            self._upload_status()
        except Exception:
            logger.warning("could not publish the run status", exc_info=True)
        finally:
            if acquired:
                self._publish_lock.release()

    def _upload_status(self) -> None:
        with self._lock:
            current = self._status.model_copy(update={"updated_at": self._now()})
        key = layout.run_status_key(current.run_id)
        self._store.write(key, current.model_dump_json().encode(), JSON_CONTENT_TYPE)
        if self._log_file.exists():
            log_key = layout.run_log_key(current.run_id)
            self._store.write(log_key, self._log_file.read_bytes(), LOG_CONTENT_TYPE)

    def _update(self, *, lock_timeout_s: float = -1, **changes: object) -> None:
        with self._lock:
            merged = {**self._status.model_dump(), **changes, "updated_at": self._now()}
            self._status = RunStatus.model_validate(merged)
        self._publish(lock_timeout_s)

    def _beat(self) -> None:
        while not self._stop.wait(HEARTBEAT_INTERVAL_S):
            self._publish()


def start_status(store: ObjectStore, config: "WorkerConfig", now: Callable[[], datetime]) -> StatusReporter:
    """Publish the "starting" status of a run and start its heartbeat."""
    started_at = now()
    status = RunStatus(
        run_id=config.run_id,
        pod_id=config.pod_id,
        state="starting",
        started_at=started_at,
        updated_at=started_at,
        deadline_at=started_at + timedelta(seconds=config.max_runtime_s),
        done_jobs=0,
        total_jobs=0,
    )
    reporter = StatusReporter(store, status, config.work_dir / LOG_FILE_NAME, now)
    reporter.start_heartbeat()
    return reporter
