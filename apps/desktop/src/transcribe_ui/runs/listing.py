"""`list_runs`: the latest runs as stored (plan m6 §1 "Backend additions", architecture §3.7).

Reads only the database (no Runpod or R2 call, no lock): what the last `refresh_status` stored of
each run (worker report, deadline stop error, refresh time) and its end. Elapsed time and cost run
to the run's end, else to its last refresh, so the answer never depends on the moment of the
request; the cloud page shows the last run's outcome and kept worker log after a restart.
"""

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runs.classes import ListRunsRequest
from transcribe_ui.runs.progress import latest_runs
from transcribe_ui.runs.responses import ListRunsResponse


def list_runs(request: ListRunsRequest, context: RequestContext) -> ListRunsResponse:
    """Return the latest runs, newest first, as stored."""
    return ListRunsResponse(runs=latest_runs(context.ports.state, request.limit))
