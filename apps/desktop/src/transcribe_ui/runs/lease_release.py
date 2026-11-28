"""Deleting the lease of a run that became final (architecture §3.9 "Run scope", "Lease removal").

A run becomes final only once its pod is verified gone or never existed (§3.7), so its lease no
longer guards anything; deleting it lets a run that never wrote `status.json` (cancelled while
queued, `launch_failed`) stop blocking Start here and on other computers before its deadline.
Every request that can end a run (`launch_worker`, `cancel_worker`, `terminate_pod`,
`cancel_queued_pod`, `reconcile_cloud`, `refresh_status`) runs under `releasing_leases`: it notes
which runs are final when it starts, and deletes the lease of each run that is final since (a
run the request created included), after the final status is committed, under the runs lock and
outside any transaction. R2 is opened lazily; an R2 or credentials failure is logged and never
fails or reverts the request (collection, which deletes all of `runs/<run_id>/`, or the next
refresh's live-lease check bound it).
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import select

from transcribe_ui.api.errors import DomainError
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runs.cloud import LazyStore
from transcribe_ui.runs.constants import FINAL_RUN_STATUSES
from transcribe_ui.runs.leases import delete_lease
from transcribe_ui.state.models import RunRow

logger = logging.getLogger(__name__)


class EndedRuns:
    """The runs final when it last looked; `release` deletes the leases of the runs final since."""

    def __init__(self, context: RequestContext, store: LazyStore) -> None:
        """Note the runs that are final now."""
        self._context = context
        self._store = store
        self._final = self._final_runs()

    def release(self) -> None:
        """Delete the lease of every run that became final since the last look, best effort."""
        try:
            final = self._final_runs()
        except DomainError as error:
            logger.warning("Run leases not released: the runs could not be read (%s)", error.code)
            return
        ended = sorted(final - self._final)
        self._final = final
        for run_id in ended:
            self._delete(run_id)

    def _delete(self, run_id: str) -> None:
        try:
            delete_lease(self._store.get(), run_id)
        except DomainError as error:
            logger.warning("The lease of run %s was not deleted (%s); collection removes it", run_id, error.code)

    def _final_runs(self) -> frozenset[str]:
        query = select(RunRow.run_id).where(RunRow.status.in_(FINAL_RUN_STATUSES))
        with self._context.ports.state.session() as session:
            return frozenset(session.scalars(query))


@contextmanager
def releasing_leases(context: RequestContext, store: LazyStore | None = None) -> Iterator[EndedRuns]:
    """Release the leases of the runs that become final in the block (also when it fails).

    The caller holds the runs lock. `store` shares the request's lazily opened R2 (a new one by
    default).
    """
    ended = EndedRuns(context, store or LazyStore(context))
    try:
        yield ended
    finally:
        ended.release()
