"""Minimal Runpod REST v2 client for self-termination (standard library only).

Base URL `https://api.runpod.io` + `/v2/pods/{id}` (OpenAPI `servers`); REST v1 was retired.
"""

import json
import logging
import time
from collections.abc import Callable
from http.client import HTTPException
from typing import NoReturn, Protocol
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, OpenerDirector, Request, build_opener

from transcribe_worker.entry.constants import (
    API_HOST_SUFFIX,
    HTTP_NOT_FOUND,
    HTTP_TIMEOUT_S,
    LOCAL_TEST_HOST,
    RETRY_INITIAL_DELAY_S,
    RETRY_MAX_DELAY_S,
    STOP_AFTER_FAILURES,
    TERMINATED_STATUS,
)

logger = logging.getLogger(__name__)

type JsonBody = dict[str, str] | None


class PodApiError(Exception):
    """A Runpod API call failed (network error, unexpected HTTP status, unknown pod)."""


class PodApi(Protocol):
    """The pod operations the entry and the watchdog need."""

    def exists(self) -> bool:
        """Return True if the API knows the pod (start-up probe)."""

    def terminate(self) -> None:
        """Ask Runpod to terminate the pod."""

    def stop(self) -> None:
        """Ask Runpod to stop the pod (fallback when terminate keeps failing)."""

    def is_gone(self) -> bool:
        """Return True once the pod is terminated."""


class NoRedirect(HTTPRedirectHandler):
    """Refuse redirects, so the Authorization header never travels to another URL."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        """Do not follow; the 3xx then surfaces as an HTTPError."""


class ApiSession:
    """Authenticated requests to one Runpod API base URL.

    The key is only sent to an allowed base URL (https on `*.runpod.io`, or http on 127.0.0.1 in tests), redirects are refused, and
    error messages never contain request or response data.
    """

    def __init__(self, base_url: str, api_key: str) -> None:
        """Create a session.

        Args:
            base_url: API base URL: https on a runpod.io host, or http on 127.0.0.1 (tests).
            api_key: Bearer token.

        Raises:
            ValueError: If the base URL is not allowed.
        """
        _check_base_url(base_url)
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._opener: OpenerDirector = build_opener(NoRedirect)

    def request(
        self,
        method: str,
        path: str,
        body: JsonBody = None,
        *,
        what: str = "pod",
    ) -> bytes | None:
        """Send one request; return the body, or None on 404.

        Args:
            method: HTTP method.
            path: Path below the base URL (with a leading slash and any query string).
            body: JSON body, or None for no body.
            what: Short label of the target, used in error messages.

        Raises:
            PodApiError: Network or protocol error, or an HTTP status other than 2xx and 404.
        """
        payload = None if body is None else json.dumps(body).encode()
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        url = f"{self._base_url}{path}"
        try:
            request = Request(url, data=payload, headers=headers, method=method)  # noqa: S310 - URL checked
            with self._opener.open(request, timeout=HTTP_TIMEOUT_S) as response:
                response_body: bytes = response.read()
        except HTTPError as error:
            if error.code == HTTP_NOT_FOUND:
                return None
            msg = f"{method} {what}: HTTP {error.code}"
            raise PodApiError(msg) from None
        except (OSError, HTTPException, ValueError) as error:  # network, protocol, header encoding
            # Only the type: messages may echo request data, and the chain stays out of logs.
            msg = f"{method} {what}: {type(error).__name__}"
            raise PodApiError(msg) from None
        return response_body


class RunpodPodApi:
    """`PodApi` over Runpod REST v2 with `urllib`.

    A 404 only means "gone" once the pod was seen (a successful GET or DELETE): with a wrong
    base URL or pod id every call returns 404, which must not look like a terminated pod.
    """

    def __init__(self, base_url: str, api_key: str, pod_id: str) -> None:
        """Create a client for one pod.

        Args:
            base_url: API base URL: https on a runpod.io host, or http on 127.0.0.1 (tests).
            api_key: Bearer token allowed to terminate the pod.
            pod_id: The pod's id.

        Raises:
            ValueError: If the base URL is not allowed.
        """
        self._session = ApiSession(base_url, api_key)
        self._pod_path = f"/pods/{pod_id}"
        self._seen = False

    def exists(self) -> bool:
        """Probe the pod (`GET /pods/{id}`)."""
        found = self._session.request("GET", self._pod_path) is not None
        self._seen = self._seen or found
        return found

    def terminate(self) -> None:
        """Terminate the pod (`DELETE /pods/{id}`)."""
        if self._session.request("DELETE", self._pod_path) is not None:
            self._seen = True
        elif not self._seen:
            _unknown_pod()

    def stop(self) -> None:
        """Stop the pod (`POST /pods/{id}/action`)."""
        self._session.request("POST", f"{self._pod_path}/action", {"action": "stop"})

    def is_gone(self) -> bool:
        """Return True if the pod is terminated (`GET /pods/{id}`)."""
        response = self._session.request("GET", self._pod_path)
        if response is None:
            if not self._seen:
                _unknown_pod()
            return True
        self._seen = True
        try:
            pod: object = json.loads(response)
        except ValueError:
            msg = "unreadable pod status"
            raise PodApiError(msg) from None
        return isinstance(pod, dict) and pod.get("status") == TERMINATED_STATUS


def _check_base_url(base_url: str) -> None:
    parts = urlsplit(base_url)
    host = parts.hostname or ""
    secure = parts.scheme == "https" and host.endswith(API_HOST_SUFFIX)
    local = parts.scheme == "http" and host == LOCAL_TEST_HOST
    if parts.username or parts.password or not (secure or local):
        # Scheme and host only: the full URL may carry credentials (user, password, query).
        msg = f"refusing API base URL (scheme {parts.scheme!r}, host {host!r})"
        raise ValueError(msg)


def _unknown_pod() -> NoReturn:
    msg = "the API does not know this pod: check RUNPOD_POD_ID and RUNPOD_API_BASE_URL"
    raise PodApiError(msg)


def terminate_until_gone(api: PodApi, sleep: Callable[[float], None] = time.sleep) -> None:
    """Terminate the pod and return only once it is gone; never gives up.

    After `STOP_AFTER_FAILURES` failed attempts, every further attempt also asks for "stop",
    which ends the container as well. The wait between attempts doubles up to a cap.

    Args:
        api: The pod API.
        sleep: Waits the given number of seconds (injected by tests).
    """
    failures = 0
    delay = RETRY_INITIAL_DELAY_S
    while True:
        if _attempt_terminate(api):
            return
        failures += 1
        if failures >= STOP_AFTER_FAILURES:
            _attempt_stop(api)
        sleep(delay)
        delay = min(delay * 2, RETRY_MAX_DELAY_S)


def _attempt_terminate(api: PodApi) -> bool:
    try:
        api.terminate()
        gone = api.is_gone()
    except Exception as error:  # noqa: BLE001 - termination must never stop retrying
        logger.error("terminate attempt failed: %s", error)  # noqa: TRY400 - message only, no traceback
        return False
    if not gone:
        logger.warning("terminate accepted, pod not gone yet")
    return gone


def _attempt_stop(api: PodApi) -> None:
    try:
        api.stop()
    except Exception as error:  # noqa: BLE001 - termination must never stop retrying
        logger.error("stop attempt failed: %s", error)  # noqa: TRY400 - message only, no traceback
