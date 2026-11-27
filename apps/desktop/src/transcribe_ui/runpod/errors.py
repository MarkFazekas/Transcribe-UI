"""Failures of Runpod calls.

Messages name the call and the HTTP status or the exception type only: never a URL, a header,
a request body or a response body (pod bodies carry secrets in their `env`).
"""


class RunpodApiError(Exception):
    """A Runpod call failed.

    For a create, a plain `RunpodApiError` means Runpod refused it (nothing was created); an
    outcome that may have created the object is an `AmbiguousCreateError`.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        scope: str | None = None,
    ) -> None:
        """Keep the message, the HTTP status (None without a response) and the call's key scope."""
        super().__init__(message)
        self.message = message
        self.status = status
        self.scope = scope


class AmbiguousCreateError(RunpodApiError):
    """A create was sent but its outcome is unknown, or it succeeded with an unreadable body.

    Timeout, 5xx, redirect, connection drop after sending, or a 2xx whose body does not parse.

    The object may exist (possibly not yet listed); the call is never sent again. Callers look
    the object up by name later instead.
    """
