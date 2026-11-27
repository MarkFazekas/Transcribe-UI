"""Handlers of `store_credentials`, `credentials_status` and `check_credentials` (DB-free, §3.6)."""

from transcribe_ui.api.errors import DomainError
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.credentials.classes import (
    CheckCredentialsRequest,
    CheckCredentialsResponse,
    CredentialsStatusRequest,
    CredentialsStatusResponse,
    ServiceCheck,
    StoreCredentialsRequest,
    StoreCredentialsResponse,
)
from transcribe_ui.credentials.constants import ERROR_CREDENTIALS_MISSING, R2_CHECK_PASSED
from transcribe_ui.storage.client import CHECK_LIMITS, open_object_store


def store_credentials(request: StoreCredentialsRequest, context: RequestContext) -> StoreCredentialsResponse:
    """Store the request's credentials in the keyring; the response names them, never the values."""
    stored = context.ports.credentials.store(request.to_store())
    return StoreCredentialsResponse(stored=stored)


def credentials_status(_request: CredentialsStatusRequest, context: RequestContext) -> CredentialsStatusResponse:
    """Report which credentials are set and where from."""
    keyring, statuses = context.ports.credentials.statuses()
    return CredentialsStatusResponse(keyring=keyring, credentials=statuses)


def check_credentials(_request: CheckCredentialsRequest, context: RequestContext) -> CheckCredentialsResponse:
    """Try the credentials: R2 lists one key of the bucket (short timeouts, 2 attempts)."""
    context.check_cancelled()
    return CheckCredentialsResponse(checks=(_check_r2(context),))


def _check_r2(context: RequestContext) -> ServiceCheck:
    ports = context.ports
    try:
        open_object_store(ports.credentials, ports.r2_clients, CHECK_LIMITS).check_access()
    except DomainError as error:
        outcome = "missing" if error.code == ERROR_CREDENTIALS_MISSING else "failed"
        return ServiceCheck(service="r2", outcome=outcome, code=error.code, message=error.message)
    return ServiceCheck(service="r2", outcome="ok", message=R2_CHECK_PASSED)
