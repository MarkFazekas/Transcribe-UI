"""The Runpod half of `check_credentials`: does the stored key work? (`GET /v2/pods?limit=1`).

The check uses short limits (2 attempts, 5 s connect, 10 s read, waits of at most 1 s), like
the R2 check, stops on the request's cancel token, and never parses the pod list (pods carry
secrets in their `env`).
"""

from transcribe_ui.api.errors import DomainError
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.credentials.checks import failed_check
from transcribe_ui.credentials.classes import ServiceCheck
from transcribe_ui.runpod.access import api_failure
from transcribe_ui.runpod.constants import RUNPOD_CHECK_PASSED, RUNPOD_CHECK_PASSED_CODE
from transcribe_ui.runpod.errors import RunpodApiError


def check_runpod(context: RequestContext) -> ServiceCheck:
    """Read one entry of the pod list with the stored key."""
    ports = context.ports
    try:
        api_key = ports.credentials.runpod_api_key()
    except DomainError as error:
        return failed_check("runpod", error)
    try:
        with ports.runpod.check_client(api_key, context.cancel.is_cancelled) as client:
            client.pods.probe()
    except RunpodApiError as error:
        failure = api_failure(error)
    else:
        return ServiceCheck(service="runpod", outcome="ok", code=RUNPOD_CHECK_PASSED_CODE, message=RUNPOD_CHECK_PASSED)
    return failed_check("runpod", failure)
