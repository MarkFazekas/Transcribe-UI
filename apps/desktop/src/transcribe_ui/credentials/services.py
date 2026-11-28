"""Handlers of `store_credentials`, `credentials_status` and `check_credentials` (DB-free, §3.6)."""

from transcribe_ui.api.errors import DomainError
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.credentials.checks import failed_check
from transcribe_ui.credentials.classes import (
    CheckCredentialsRequest,
    CheckCredentialsResponse,
    CredentialsStatusRequest,
    CredentialsStatusResponse,
    ServiceCheck,
    StoreCredentialsRequest,
    StoreCredentialsResponse,
)
from transcribe_ui.credentials.constants import R2_CHECK_PASSED, R2_CHECK_PASSED_CODE
from transcribe_ui.runpod.check import check_runpod
from transcribe_ui.storage.client import CHECK_LIMITS, open_object_store


def store_credentials(request: StoreCredentialsRequest, context: RequestContext) -> StoreCredentialsResponse:
    """Store the request's credentials in the keyring; the response names them, never the values."""
    stored = context.ports.credentials.store(request.to_store())
    return StoreCredentialsResponse(stored=stored)


def credentials_status(_request: CredentialsStatusRequest, context: RequestContext) -> CredentialsStatusResponse:
    """Report which credentials are set and where from, and whether the Runpod key counts as set."""
    lookup = context.ports.credentials.lookup()
    return CredentialsStatusResponse(
        keyring=lookup.keyring,
        credentials=lookup.statuses(),
        runpod_key_set=lookup.runpod_key_set(),
        r2_set=lookup.r2_set(),
    )


def check_credentials(_request: CheckCredentialsRequest, context: RequestContext) -> CheckCredentialsResponse:
    """Try the credentials: R2 lists one key of the bucket, Runpod one pod (short timeouts, 2 attempts)."""
    context.check_cancelled()
    r2 = _check_r2(context)
    context.check_cancelled()
    return CheckCredentialsResponse(checks=(r2, check_runpod(context)))


def _check_r2(context: RequestContext) -> ServiceCheck:
    ports = context.ports
    try:
        open_object_store(ports.credentials, ports.r2_clients, CHECK_LIMITS).check_access()
    except DomainError as error:
        return failed_check("r2", error)
    return ServiceCheck(service="r2", outcome="ok", code=R2_CHECK_PASSED_CODE, message=R2_CHECK_PASSED)
