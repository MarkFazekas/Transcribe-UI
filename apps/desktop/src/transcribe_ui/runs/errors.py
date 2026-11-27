"""Errors of the run-activity check and of the run requests."""

from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import JsonValue

from transcribe_ui.api.errors import DomainError
from transcribe_ui.runpod.access import api_failure
from transcribe_ui.runpod.constants import ERROR_CLOUD_UNREACHABLE, HTTP_FORBIDDEN, HTTP_UNAUTHORIZED
from transcribe_ui.runpod.errors import RunpodApiError
from transcribe_ui.runs.classes import ActivitySource, RunpodKind
from transcribe_ui.runs.constants import (
    ERROR_LAUNCH_REFUSED,
    ERROR_LAUNCH_UNRESOLVED,
    ERROR_NOT_APP_POD,
    ERROR_NOTHING_TO_LAUNCH,
    ERROR_POD_NOT_FOUND,
    ERROR_RUN_ACTIVE,
    ERROR_RUN_NOT_ACTIVE,
    ERROR_RUN_NOT_FOUND,
    ERROR_STOP_UNVERIFIED,
)

# Which part of the cloud could not be read.
type CloudPart = Literal["runpod", "r2"]


class ActivityUnknownError(DomainError):
    """The Runpod lists or R2 could not be read, so a run may be active: the check fails closed."""

    def __init__(self, part: CloudPart, cause: str) -> None:
        """Name the part and the cause (an error code, never request data) in the details."""
        super().__init__(
            ERROR_CLOUD_UNREACHABLE,
            "Whether a worker run is active cannot be checked, so nothing was started or published; try again later.",
            {"part": part, "cause": cause},
        )


class RunActiveError(DomainError):
    """A worker run is active, so the request would change what it may be working on; nothing was changed."""

    def __init__(self, sources: Sequence[ActivitySource]) -> None:
        """Name the active runs and every sign of them in the details."""
        named = {source.run_id for source in sources}
        run_ids: list[JsonValue] = []
        run_ids.extend(sorted(run_id for run_id in named if run_id is not None))
        signs: list[JsonValue] = []
        signs.extend(_sign(source) for source in sources)
        super().__init__(
            ERROR_RUN_ACTIVE,
            "A worker run is active; nothing was changed. Try again once it has ended.",
            {"run_ids": run_ids, "sources": signs},
        )


def _sign(source: ActivitySource) -> JsonValue:
    sign: dict[str, JsonValue] = {"kind": source.kind, "name": source.name}
    sign.update({"status": source.status, "run_id": source.run_id})
    return sign


def runpod_call[ResultT](call: Callable[[], ResultT]) -> ResultT:
    """Return the call's result; a failed Runpod call becomes its domain error.

    Raises:
        DomainError: `runpod_key_rejected`, `runpod_key_missing_scope` or `cloud_unreachable`
            (raised outside the `except` block, so nothing of the call is chained).
    """
    try:
        return call()
    except RunpodApiError as error:
        failure = api_failure(error)
    raise failure


class NothingToLaunchError(DomainError):
    """No manifest has a pending job, so no worker is started."""

    def __init__(self) -> None:
        """Use the fixed code and message."""
        super().__init__(ERROR_NOTHING_TO_LAUNCH, "There is nothing to transcribe, so no worker was started.")


class LaunchRefusedError(DomainError):
    """Runpod refused the pod request (no capacity, no balance, a rule): nothing was created."""

    def __init__(self, run_id: str, status: int | None) -> None:
        """Name the run (now `launch_failed`) and the HTTP status (None: Runpod was not reached)."""
        super().__init__(
            ERROR_LAUNCH_REFUSED,
            "Runpod did not create the worker; nothing was started. Check the Runpod account, then try again.",
            {"run_id": run_id, "http_status": status},
        )


class RunNotFoundError(DomainError):
    """The request names a run this database does not know."""

    def __init__(self, run_id: str) -> None:
        """Name the run."""
        super().__init__(ERROR_RUN_NOT_FOUND, "There is no run with this id.", {"run_id": run_id})


class RunNotActiveError(DomainError):
    """The run has ended already; there is nothing to stop."""

    def __init__(self, run_id: str, status: str) -> None:
        """Name the run and its status."""
        super().__init__(
            ERROR_RUN_NOT_ACTIVE,
            "The run has ended already; there is nothing to stop.",
            {"run_id": run_id, "run_status": status},
        )


class LaunchUnresolvedError(DomainError):
    """The run's pod request has an unknown outcome and Runpod does not list its pod (yet)."""

    def __init__(self, run_id: str) -> None:
        """Name the run (still `launching`)."""
        super().__init__(
            ERROR_LAUNCH_UNRESOLVED,
            "Whether Runpod created this run's worker is not known yet, so it cannot be stopped; "
            "reconcile the cloud in a minute and try again.",
            {"run_id": run_id},
        )


class StopUnverifiedError(DomainError):
    """A terminate or cancel was sent, but reading the pod again does not show it done."""

    def __init__(self, kind: RunpodKind, object_id: str) -> None:
        """Name what was stopped; the run stays as it was, so it is still followed."""
        super().__init__(
            ERROR_STOP_UNVERIFIED,
            "The worker may still be running: Runpod does not show it stopped yet. Try again.",
            {"kind": kind, "object_id": object_id},
        )


class PodNotFoundError(DomainError):
    """Runpod does not know the pod or queued pod."""

    def __init__(self, kind: RunpodKind, object_id: str) -> None:
        """Name it."""
        super().__init__(
            ERROR_POD_NOT_FOUND, "Runpod does not know this worker.", {"kind": kind, "object_id": object_id}
        )


class NotAppPodError(DomainError):
    """The pod or queued pod is not one of the app's, so the app leaves it alone."""

    def __init__(self, kind: RunpodKind, object_id: str) -> None:
        """Name it (not its name: a foreign name may hold anything)."""
        super().__init__(
            ERROR_NOT_APP_POD,
            "This is not one of the app's workers; manage it in the Runpod console.",
            {"kind": kind, "object_id": object_id},
        )


def launch_refusal(run_id: str, error: RunpodApiError) -> DomainError:
    """Return the error of a pod request Runpod refused (nothing was created).

    A rejected key, a missing scope or an unreachable API keep their own codes; any other
    refusal (no capacity, no balance, queue full, a rule) is `launch_refused`.
    """
    if error.status in {HTTP_UNAUTHORIZED, HTTP_FORBIDDEN, None}:
        return api_failure(error)
    return LaunchRefusedError(run_id, error.status)
