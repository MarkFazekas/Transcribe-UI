"""Runpod REST v2 calls of the reaper, over the entry's hardened `ApiSession`."""

import json
from typing import Protocol
from urllib.parse import quote, urlencode

from pydantic import ValidationError

from transcribe_worker.entry.constants import TERMINATED_STATUS
from transcribe_worker.entry.pod_api import ApiSession, PodApiError
from transcribe_worker.reaper.classes import ListedPod, Listing, Page, PodPage, QueuedPodPage
from transcribe_worker.reaper.constants import CURSOR_PARAMETER, MAX_PAGES, PODS_PATH, QUEUED_PODS_PATH, QUEUED_STATUS


class ReaperApi(Protocol):
    """The Runpod operations one reaper pass needs. Every failure is a PodApiError."""

    def list_pods(self) -> Listing:
        """Return every pod of the account (all pages)."""

    def list_queued_pods(self) -> Listing:
        """Return every queued pod of the account (all pages, any status)."""

    def terminate_pod(self, pod_id: str) -> None:
        """Terminate a pod; a pod that no longer exists counts as done."""

    def pod_gone(self, pod_id: str) -> bool:
        """Return True once the pod is terminated or no longer known."""

    def cancel_queued_pod(self, queued_pod_id: str) -> None:
        """Cancel a queued pod; one that no longer exists counts as done."""

    def queued_pod_resolved(self, queued_pod_id: str) -> bool:
        """Return True once the queued pod no longer waits for capacity."""


class RunpodReaperApi:
    """`ReaperApi` over Runpod REST v2.

    A 404 for a single pod means "gone": the pod was just seen in the list, so a 404 cannot
    come from a wrong base URL (the list would have failed first).
    """

    def __init__(self, base_url: str, api_key: str) -> None:
        """Create the client.

        Args:
            base_url: API base URL: https on a runpod.io host, or http on 127.0.0.1 (tests).
            api_key: Pods-only bearer token.

        Raises:
            ValueError: If the base URL is not allowed.
        """
        self._session = ApiSession(base_url, api_key)

    def list_pods(self) -> Listing:
        """List pods (`GET /pods`, following `pagination.nextCursor`)."""
        return self._list(PODS_PATH, PodPage, "pods")

    def list_queued_pods(self) -> Listing:
        """List queued pods (`GET /queued-pods`, following `pagination.nextCursor`)."""
        return self._list(QUEUED_PODS_PATH, QueuedPodPage, "queued pods")

    def terminate_pod(self, pod_id: str) -> None:
        """Terminate a pod (`DELETE /pods/{id}`)."""
        self._session.request("DELETE", _item_path(PODS_PATH, pod_id), what="pod")

    def pod_gone(self, pod_id: str) -> bool:
        """Return True on 404 or status TERMINATED (`GET /pods/{id}`)."""
        return self._status(PODS_PATH, pod_id, "pod") in {None, TERMINATED_STATUS}

    def cancel_queued_pod(self, queued_pod_id: str) -> None:
        """Cancel a queued pod (`DELETE /queued-pods/{id}`)."""
        self._session.request("DELETE", _item_path(QUEUED_PODS_PATH, queued_pod_id), what="queued pod")

    def queued_pod_resolved(self, queued_pod_id: str) -> bool:
        """Return True on 404 or any status but QUEUED (`GET /queued-pods/{id}`)."""
        return self._status(QUEUED_PODS_PATH, queued_pod_id, "queued pod") != QUEUED_STATUS

    def _list(self, path: str, page_model: type[Page], what: str) -> Listing:
        listed: list[ListedPod] = []
        dropped = 0
        query = ""
        for _ in range(MAX_PAGES):
            body = self._get(f"{path}{query}", what)
            page = _parse_page(page_model, body, what)
            listed.extend(page.entries)
            dropped += page.dropped
            if not page.pagination.has_next_page:
                return Listing(entries=listed, dropped=dropped)
            if not page.pagination.next_cursor:
                msg = f"GET {what}: next page announced without a cursor"
                raise PodApiError(msg)
            query = f"?{urlencode({CURSOR_PARAMETER: page.pagination.next_cursor})}"
        msg = f"GET {what}: more than {MAX_PAGES} pages"
        raise PodApiError(msg)

    def _get(self, path: str, what: str) -> bytes:
        body = self._session.request("GET", path, what=what)
        if body is None:
            msg = f"GET {what}: HTTP 404"
            raise PodApiError(msg)
        return body

    def _status(self, path: str, item_id: str, what: str) -> str | None:
        body = self._session.request("GET", _item_path(path, item_id), what=what)
        if body is None:
            return None
        try:
            parsed: object = json.loads(body)
        except ValueError:
            msg = f"GET {what}: unreadable response"
            raise PodApiError(msg) from None
        status = parsed.get("status") if isinstance(parsed, dict) else None
        if not isinstance(status, str):
            msg = f"GET {what}: response without a status"
            raise PodApiError(msg)
        return status


def _parse_page(page_model: type[Page], body: bytes, what: str) -> Page:
    try:
        return page_model.model_validate_json(body)
    except ValidationError:  # its message echoes response data: never pass it on
        msg = f"GET {what}: unexpected response shape"
        raise PodApiError(msg) from None


def _item_path(path: str, item_id: str) -> str:
    # The id comes from an API response: quote it so it can only ever be one path segment.
    return f"{path}/{quote(item_id, safe='')}"
