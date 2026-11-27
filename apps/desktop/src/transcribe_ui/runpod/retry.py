"""Retry policy of Runpod calls (architecture §3.7, `research/runpod.md` §2).

- Reads, deletes and updates (idempotent): a 429 waits for `Retry-After`, a server error or a
  network failure backs off, up to `MAX_ATTEMPTS`; any other client error is final.
- Creates are never sent twice after an ambiguous outcome: only a 429 (refused, nothing
  created) and a failure to connect (nothing sent) are retried. A timeout, a server error, a
  redirect or a connection drop after sending raises `AmbiguousCreateError`, since the object
  may exist without being listed yet.
"""

from dataclasses import dataclass

import httpx

from transcribe_ui.runpod.calls import Call
from transcribe_ui.runpod.constants import (
    HTTP_CLIENT_ERROR,
    HTTP_FIRST_NON_SUCCESS,
    HTTP_NOT_FOUND,
    HTTP_SERVER_ERROR,
    HTTP_TOO_MANY_REQUESTS,
    NOT_SENT_ERRORS,
    RETRY_AFTER_MAX_S,
    RETRY_INITIAL_DELAY_S,
    RETRY_MAX_DELAY_S,
)
from transcribe_ui.runpod.errors import AmbiguousCreateError, RunpodApiError


@dataclass(frozen=True)
class Retry:
    """Decision to try a call again after a delay."""

    delay_s: float
    reason: str


def backoff(attempt: int) -> float:
    """Return the delay after the given failed attempt (1, 2, 4, ... seconds, capped)."""
    delay = RETRY_INITIAL_DELAY_S * 2 ** (attempt - 1)
    return float(min(delay, RETRY_MAX_DELAY_S))


def retry_after(response: httpx.Response, attempt: int) -> float:
    """Return the delay a 429 asks for: delta seconds, capped; otherwise the backoff delay."""
    text = response.headers.get("Retry-After", "").strip()
    try:
        seconds = float(text)
    except ValueError:  # an HTTP date or nothing
        return backoff(attempt)
    return float(min(max(seconds, 0), RETRY_AFTER_MAX_S))


def judge_failure(call: Call, error: httpx.RequestError, *, last: bool, attempt: int) -> Retry:
    """Return when to retry after a transport failure.

    Raises:
        AmbiguousCreateError: A create failed after it may have been sent.
        RunpodApiError: No attempt is left.
    """
    # Only the type: exception messages may hold the URL or other request data.
    reason = type(error).__name__
    if call.creates and not isinstance(error, NOT_SENT_ERRORS):
        msg = f"{call.label}: {reason} after the request was sent; it may have taken effect"
        raise AmbiguousCreateError(msg, scope=call.scope) from None
    if last:
        msg = f"{call.label}: {reason}"
        raise RunpodApiError(msg, scope=call.scope) from None
    return Retry(backoff(attempt), reason)


def judge_response(
    call: Call,
    response: httpx.Response,
    *,
    last: bool,
    attempt: int,
) -> httpx.Response | Retry | None:
    """Return the response if it succeeded, None for an allowed 404, or when to retry.

    Raises:
        AmbiguousCreateError: A create got a server error or a redirect.
        RunpodApiError: The call failed for good (redirects are never followed).
    """
    status = response.status_code
    if status < HTTP_FIRST_NON_SUCCESS:
        return response
    if status == HTTP_NOT_FOUND and call.missing_ok:
        return None
    reason = f"HTTP {status}"
    if status == HTTP_TOO_MANY_REQUESTS and not last:
        return Retry(retry_after(response, attempt), reason)
    server_error = status >= HTTP_SERVER_ERROR
    if call.creates and (server_error or status < HTTP_CLIENT_ERROR):
        msg = f"{call.label}: {reason}; it may have taken effect"
        raise AmbiguousCreateError(msg, status=status, scope=call.scope)
    if server_error and not last:
        return Retry(backoff(attempt), reason)
    msg = f"{call.label}: {reason}"
    raise RunpodApiError(msg, status=status, scope=call.scope)
