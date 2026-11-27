"""The Runpod half of `check_credentials`: does the stored key work? (`GET /v2/pods`).

The request itself belongs to the credential store (M5.5); whichever of the two parts merges
second puts this result into its response.
"""

from transcribe_ui.runpod.access import RunpodAccess
from transcribe_ui.runpod.classes import RunpodKeyCheck
from transcribe_ui.runpod.client import RunpodClient
from transcribe_ui.runpod.constants import HTTP_FORBIDDEN, HTTP_UNAUTHORIZED
from transcribe_ui.runpod.errors import RunpodApiError


def check_runpod_key(access: RunpodAccess) -> RunpodKeyCheck:
    """Read one entry of the pod list with the stored key; the pod (and its env) is never parsed."""
    api_key = access.credentials.runpod_api_key()
    if api_key is None:
        return RunpodKeyCheck(state="missing", message="No Runpod API key is stored.")
    try:
        with RunpodClient(api_key, transport=access.transport, timing=access.timing()) as client:
            client.pods.probe()
    except RunpodApiError as error:
        return _failed(error)
    return RunpodKeyCheck(state="ok", message="The Runpod API key works.")


def _failed(error: RunpodApiError) -> RunpodKeyCheck:
    if error.status == HTTP_UNAUTHORIZED:
        return RunpodKeyCheck(state="rejected", message="Runpod rejected the API key.")
    if error.status == HTTP_FORBIDDEN:
        message = f"The Runpod API key lacks the permission {error.scope!r}."
        return RunpodKeyCheck(state="missing_scope", message=message, scope=error.scope)
    return RunpodKeyCheck(state="unreachable", message=f"Runpod could not be reached ({error.message}).")
