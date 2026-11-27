"""The dispatch table of the backend: request `action` -> handler (architecture §3.1)."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from transcribe_ui.api.classes import Response
from transcribe_ui.api.local_handlers import RequestRunner, local_handlers
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches import exclusion, listing, review, services
from transcribe_ui.cleanup.services import cleanup_cloud, create_retry_batch
from transcribe_ui.placement import services as downloads
from transcribe_ui.runs import cancel, launch, monitor, planning, reconcile, stragglers
from transcribe_ui.upload.services import run_upload


@dataclass(frozen=True)
class Route[RequestT]:
    """How one request type is handled."""

    run: Callable[[RequestT, RequestContext], Response]
    # False for requests that never open the database (settings, credentials, scans, app info),
    # so a second process can run them while the GUI holds the writer lock.
    needs_database: bool


# Dispatch table: request `action` -> route.
type Routes = Mapping[str, Route[Any]]


def _local_route(run: RequestRunner) -> Route[Any]:
    return Route(run=run, needs_database=False)


def default_routes() -> Routes:
    """Return the production dispatch table; each part adds its requests here.

    The requests that need no database are listed in `api/local_handlers.py`.
    """
    runners = local_handlers().items()
    local = {action: _local_route(run) for action, run in runners}
    return MappingProxyType(
        {
            **local,
            "create_batch": Route(run=services.create_batch, needs_database=True),
            "edit_batch": Route(run=services.edit_batch, needs_database=True),
            "delete_batch": Route(run=services.delete_batch, needs_database=True),
            "start_review": Route(run=services.start_review, needs_database=True),
            "review_batch": Route(run=review.review_batch, needs_database=True),
            "resolve_review": Route(run=review.resolve_review, needs_database=True),
            "exclude_items": Route(run=exclusion.exclude_items, needs_database=True),
            "list_batches": Route(run=listing.list_batches, needs_database=True),
            "get_batch": Route(run=listing.get_batch, needs_database=True),
            "set_batch_hidden": Route(run=listing.set_batch_hidden, needs_database=True),
            "run_upload": Route(run=run_upload, needs_database=True),
            "plan_launch": Route(run=planning.plan_launch, needs_database=True),
            "launch_worker": Route(run=launch.launch_worker, needs_database=True),
            "cancel_worker": Route(run=cancel.cancel_worker, needs_database=True),
            "refresh_status": Route(run=monitor.refresh_status, needs_database=True),
            "reconcile_cloud": Route(run=reconcile.reconcile_cloud, needs_database=True),
            "terminate_pod": Route(run=stragglers.terminate_pod, needs_database=True),
            "cancel_queued_pod": Route(run=stragglers.cancel_queued_pod, needs_database=True),
            "plan_download": Route(run=downloads.plan_download, needs_database=True),
            "run_download": Route(run=downloads.run_download, needs_database=True),
            "export_custom_plan": Route(run=downloads.export_custom_plan, needs_database=True),
            "import_custom_plan": Route(run=downloads.import_custom_plan, needs_database=True),
            "create_retry_batch": Route(run=create_retry_batch, needs_database=True),
            "cleanup_cloud": Route(run=cleanup_cloud, needs_database=True),
        },
    )
