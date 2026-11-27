"""Pods and queued pods of the Runpod API (architecture §3.7)."""

from transcribe_ui.runpod.bodies import CreatePod
from transcribe_ui.runpod.calls import Endpoint
from transcribe_ui.runpod.constants import (
    HTTP_ACCEPTED,
    LIMIT_PARAMETER,
    PODS_PATH,
    QUEUED_PODS_PATH,
    SCOPE_PODS_READ,
    SCOPE_PODS_WRITE,
)
from transcribe_ui.runpod.session import RunpodHttp, parse_body
from transcribe_ui.runpod.wire import Pod, PodPage, QueuedPod, QueuedPodPage


class PodsApi:
    """Pods and queued pods (both under the pods scopes)."""

    def __init__(self, http: RunpodHttp) -> None:
        """Use this session."""
        self._http = http
        self._pods = Endpoint(PODS_PATH, "pod", "pods", SCOPE_PODS_READ, SCOPE_PODS_WRITE)
        self._queued = Endpoint(QUEUED_PODS_PATH, "queued pod", "queued pods", SCOPE_PODS_READ, SCOPE_PODS_WRITE)

    def list_pods(self) -> tuple[Pod, ...]:
        """Return every pod of the account (all pages; terminated ones too while listed)."""
        call = self._pods.listing()
        return self._http.collect(call, PodPage, lambda page: page.pods)

    def list_queued_pods(self) -> tuple[QueuedPod, ...]:
        """Return every queued pod of the account, whatever its status (all pages)."""
        call = self._queued.listing()
        return self._http.collect(call, QueuedPodPage, lambda page: page.queued_pods)

    def get_pod(self, pod_id: str) -> Pod | None:
        """Return the pod, or None if Runpod does not know it (404)."""
        return self._http.fetch(self._pods.read(pod_id), Pod)

    def get_queued_pod(self, queued_pod_id: str) -> QueuedPod | None:
        """Return the queued pod, or None if Runpod does not know it (404)."""
        return self._http.fetch(self._queued.read(queued_pod_id), QueuedPod)

    def create(self, body: CreatePod) -> Pod | QueuedPod:
        """Create a pod: 201 gives the pod, 202 (with `queue`) the queued pod.

        Raises:
            AmbiguousCreateError: The outcome is unknown; look the name up instead of retrying.
        """
        call = self._pods.create(body.body())
        response = self._http.require(call)
        if response.status_code == HTTP_ACCEPTED:
            return parse_body(call, response, QueuedPod)
        return parse_body(call, response, Pod)

    def terminate(self, pod_id: str) -> bool:
        """Terminate a pod; return False if Runpod does not know it (404)."""
        return self._http.send(self._pods.delete(pod_id)) is not None

    def cancel_queued(self, queued_pod_id: str) -> bool:
        """Cancel a queued pod (a pod it already created stays); False if unknown (404)."""
        return self._http.send(self._queued.delete(queued_pod_id)) is not None

    def probe(self) -> None:
        """Read one entry of the pod list, to check the key; the body is not even parsed."""
        self._http.require(self._pods.listing({LIMIT_PARAMETER: "1"}))
