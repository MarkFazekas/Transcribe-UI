"""HTTP session of the Runpod client: where the key goes, timeouts, the retry loop and parsing.

The key is sent only over https to `api.runpod.io` (exact host, default port), redirects are
never followed, and error messages carry no request or response data. Errors are raised
outside `except` blocks, so no exception chain keeps a request or a response. The retry
policy is in `runpod/retry.py`.
"""

import logging
from collections.abc import Callable
from dataclasses import replace
from types import TracebackType
from typing import Self

import httpx
from pydantic import BaseModel, SecretStr

from transcribe_ui.api.errors import CancelledError
from transcribe_ui.runpod.calls import Call, check_base_url, check_request
from transcribe_ui.runpod.constants import (
    API_BASE_URL,
    CURSOR_PARAMETER,
    HTTP_NOT_FOUND,
    MAX_PAGES,
)
from transcribe_ui.runpod.errors import RunpodApiError
from transcribe_ui.runpod.parsing import parse_body
from transcribe_ui.runpod.retry import Retry, judge_failure, judge_response
from transcribe_ui.runpod.timing import Timing
from transcribe_ui.runpod.wire import Paged

logger = logging.getLogger(__name__)


class RunpodHttp:
    """Authenticated calls to the Runpod REST v2 API."""

    def __init__(
        self,
        api_key: SecretStr,
        *,
        base_url: str = API_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        timing: Timing | None = None,
    ) -> None:
        """Create the session.

        Args:
            api_key: Bearer token.
            base_url: API base URL: https on `api.runpod.io`.
            transport: Replaces the network (tests: `httpx.MockTransport`); proxies from the
                environment are then ignored too, so a test never reaches the network.
            timing: Waits, clock and cancel check (default: real time, never cancelled).

        Raises:
            ValueError: If the base URL is not allowed or the key is not ASCII.
        """
        check_base_url(base_url)
        token = api_key.get_secret_value()
        if not token.isascii():  # the credential store refuses these; never let httpx fail on one
            msg = "the Runpod API key must be ASCII"
            raise ValueError(msg)
        self._timing = timing or Timing()
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(self._timing.read_timeout_s, connect=self._timing.connect_timeout_s),
            follow_redirects=False,
            transport=transport,
            trust_env=transport is None,
            event_hooks={"request": [check_request]},
        )

    def __enter__(self) -> Self:
        """Return the session."""
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        """Close the connections."""
        self.close()

    def close(self) -> None:
        """Close the connections."""
        self._client.close()

    def send(self, call: Call) -> httpx.Response | None:
        """Carry out a call with the retry policy; return the response, or None on an allowed 404.

        Raises:
            AmbiguousCreateError: A create may have taken effect.
            RunpodApiError: The call failed (after the retries it allows).
            CancelledError: The request was cancelled before an attempt.
        """
        attempt = 1
        while True:
            if self._timing.stopped():
                raise CancelledError
            outcome = self._attempt(call, attempt)
            if not isinstance(outcome, Retry):
                return outcome
            delay_s = min(outcome.delay_s, self._timing.wait_max_s)
            logger.warning(
                "%s: %s (attempt %d of %d); retrying in %.1f s",
                call.label,
                outcome.reason,
                attempt,
                self._timing.attempts,
                delay_s,
            )
            self._timing.wait(delay_s, self._timing.stopped)
            attempt += 1

    def require(self, call: Call) -> httpx.Response:
        """Like `send`, but a 404 is an error too."""
        response = self.send(replace(call, missing_ok=False))
        if response is None:  # `send` returns None only for `missing_ok`; kept for the type.
            msg = f"{call.label}: HTTP {HTTP_NOT_FOUND}"
            raise RunpodApiError(msg, status=HTTP_NOT_FOUND, scope=call.scope)
        return response

    def fetch[ModelT: BaseModel](self, call: Call, model: type[ModelT]) -> ModelT | None:
        """Carry out a call and parse its body; None on an allowed 404."""
        response = self.send(call)
        return None if response is None else parse_body(call, response, model)

    def fetch_one[ModelT: BaseModel](self, call: Call, model: type[ModelT]) -> ModelT:
        """Carry out a call and parse its body; a 404 is an error."""
        return parse_body(call, self.require(call), model)

    def collect[PageT: Paged, ItemT](
        self,
        call: Call,
        page_model: type[PageT],
        entries: Callable[[PageT], tuple[ItemT, ...]],
    ) -> tuple[ItemT, ...]:
        """Return every entry of a list, following `pagination.nextCursor`.

        Raises:
            RunpodApiError: A page failed, had an unexpected shape, or there were too many.
        """
        found: list[ItemT] = []
        query = dict(call.query or {})
        for _ in range(MAX_PAGES):
            page = self.fetch_one(replace(call, query=dict(query)), page_model)
            found.extend(entries(page))
            cursor = page.pagination.next_cursor
            if not page.pagination.has_next_page:
                return tuple(found)
            if not cursor:
                msg = f"{call.label}: next page announced without a cursor"
                raise RunpodApiError(msg, scope=call.scope)
            query[CURSOR_PARAMETER] = cursor
        msg = f"{call.label}: more than {MAX_PAGES} pages"
        raise RunpodApiError(msg, scope=call.scope)

    def _attempt(self, call: Call, attempt: int) -> httpx.Response | Retry | None:
        last = attempt >= self._timing.attempts
        request = call.request(self._client)
        try:
            response = self._client.send(request)
        except httpx.RequestError as error:
            failure = error
        else:
            return judge_response(call, response, last=last, attempt=attempt, now=self._timing.now)
        # Judged outside the `except` block, so a raised error does not chain the transport error.
        return judge_failure(call, failure, last=last, attempt=attempt)
