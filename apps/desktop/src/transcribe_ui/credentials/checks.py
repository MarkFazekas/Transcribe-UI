"""The result of a failed service check of `check_credentials` (shared by the R2 and Runpod checks)."""

from transcribe_ui.api.errors import DomainError
from transcribe_ui.credentials.classes import CheckedService, CheckOutcome, ServiceCheck
from transcribe_ui.credentials.constants import ERROR_CREDENTIALS_MISSING


def failed_check(service: CheckedService, error: DomainError) -> ServiceCheck:
    """Return the check of a service that failed with `error` (`missing` without credentials): its code and details."""
    outcome: CheckOutcome = "missing" if error.code == ERROR_CREDENTIALS_MISSING else "failed"
    return ServiceCheck(
        service=service,
        outcome=outcome,
        code=error.code,
        message=error.message,
        details=error.details,
    )
