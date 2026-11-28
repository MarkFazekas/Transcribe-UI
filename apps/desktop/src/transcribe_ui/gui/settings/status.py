"""What the credentials' status means for the first start and the cloud setup (Qt-free; architecture §3.9)."""

from transcribe_ui.credentials.classes import CredentialsStatusResponse
from transcribe_ui.credentials.constants import R2_CREDENTIALS
from transcribe_ui.gui.settings.constants import OPTIONAL_CREDENTIAL


def r2_missing(status: CredentialsStatusResponse) -> bool:
    """Return whether an R2 credential is set nowhere (the first-start wizard opens then)."""
    r2 = [credential for credential in status.credentials if credential.name in R2_CREDENTIALS]
    return any(credential.source == "missing" for credential in r2)


def runpod_key_set(status: CredentialsStatusResponse | None) -> bool:
    """Return whether the Runpod API key is set (the cloud setup needs it, §3.9)."""
    if status is None:
        return False
    sources = {credential.name: credential.source for credential in status.credentials}
    return sources.get(OPTIONAL_CREDENTIAL, "missing") != "missing"
