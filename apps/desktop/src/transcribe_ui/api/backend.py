"""The backend façade: one `execute` for every request (architecture §3.1)."""

import logging

from transcribe_ui.api.classes import ErrorBody, ErrorResponse, Request, Response
from transcribe_ui.api.constants import ERROR_INTERNAL, INTERNAL_ERROR_MESSAGE
from transcribe_ui.api.errors import DomainError
from transcribe_ui.api.ports import CancelFlag, CancelToken, DiscardProgress, Ports, ProgressSink, RequestContext
from transcribe_ui.api.routes import Routes, default_routes
from transcribe_ui.api.schema import parse_request

logger = logging.getLogger(__name__)


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
        sink = DiscardProgress() if progress is None else progress
        token = CancelFlag() if cancel is None else cancel
        context = RequestContext(self._ports, sink, token)
        try:
            if route.needs_database:
                self._ports.state.open_database()
            return route.run(request, context)
        except DomainError as error:
            return ErrorResponse(action=request.action, error=error.body())
        except Exception:
            logger.exception("Request %s failed unexpectedly", request.action)
            body = ErrorBody(code=ERROR_INTERNAL, message=INTERNAL_ERROR_MESSAGE)
            return ErrorResponse(action=request.action, error=body)

    def execute_json(
        self,
        document: str | bytes,
        progress: ProgressSink | None = None,
        cancel: CancelToken | None = None,
    ) -> Response:
        """Validate a JSON request document, then carry it out like `execute`."""
        request = parse_request(document)
        if isinstance(request, ErrorResponse):
            return request
        return self.execute(request, progress, cancel)
