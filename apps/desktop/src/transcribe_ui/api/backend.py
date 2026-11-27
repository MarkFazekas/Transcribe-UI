"""The backend façade: one `execute` for every request (architecture §3.1)."""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError

from transcribe_ui.api.classes import ErrorBody, ErrorResponse, Request, Response
from transcribe_ui.api.constants import ERROR_INTERNAL
from transcribe_ui.api.errors import DomainError
from transcribe_ui.api.info import get_app_info
from transcribe_ui.api.ports import CancelFlag, CancelToken, DiscardProgress, Ports, ProgressSink, RequestContext
from transcribe_ui.api.schema import invalid_request, request_adapter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Route[RequestT]:
    """How one request type is handled."""

    run: Callable[[RequestT, RequestContext], Response]
    # False for requests that never open the database (settings, credentials, scans, app info),
    # so a second process can run them while the GUI holds the writer lock.
    needs_database: bool


# Dispatch table: request `action` -> route.
type Routes = Mapping[str, Route[Any]]


def default_routes() -> Routes:
    """Return the production dispatch table; each part adds its requests here."""
    return MappingProxyType(
        {
            "get_app_info": Route(run=get_app_info, needs_database=False),
        },
    )


class Backend:
    """Carries out requests; safe to call from several threads."""

    def __init__(self, ports: Ports, routes: Routes | None = None) -> None:
        """Use these ports; `routes` is replaced only by tests."""
        self._ports = ports
        self._routes = default_routes() if routes is None else routes

    def needs_database(self, action: str) -> bool:
        """Return whether the request type opens the database."""
        return self._routes[action].needs_database

    def execute(
        self,
        request: Request,
        progress: ProgressSink | None = None,
        cancel: CancelToken | None = None,
    ) -> Response:
        """Carry out one request; failures come back as `ErrorResponse`, never as exceptions."""
        route = self._routes[request.action]
        sink = progress or DiscardProgress()
        context = RequestContext(self._ports, sink, cancel or CancelFlag())
        try:
            return route.run(request, context)
        except DomainError as error:
            return ErrorResponse(action=request.action, error=error.body())
        except Exception:
            logger.exception("Request %s failed unexpectedly", request.action)
            message = "Unexpected internal error; the details are in the log."
            return ErrorResponse(action=request.action, error=ErrorBody(code=ERROR_INTERNAL, message=message))

    def execute_json(
        self,
        document: str | bytes,
        progress: ProgressSink | None = None,
        cancel: CancelToken | None = None,
    ) -> Response:
        """Validate a JSON request document, then carry it out like `execute`."""
        try:
            request = request_adapter().validate_json(document)
        except ValidationError as error:
            return invalid_request(error)
        return self.execute(request, progress, cancel)
