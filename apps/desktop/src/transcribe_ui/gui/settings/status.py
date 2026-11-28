"""What the credentials' status means for the first start and the cloud setup (Qt-free; architecture §3.9)."""

from transcribe_ui.credentials.classes import CredentialsStatusResponse
from transcribe_ui.credentials.constants import R2_CREDENTIALS


def r2_missing(status: CredentialsStatusResponse) -> bool:
    """Return whether an R2 credential is set nowhere (the first-start wizard opens then)."""
    r2 = [credential for credential in status.credentials if credential.name in R2_CREDENTIALS]
    return any(credential.source == "missing" for credential in r2)


def runpod_key_set(status: CredentialsStatusResponse | None) -> bool:
    """Return whether the backend counts the Runpod API key as set (the cloud setup needs it, §3.9).

    The backend's rule: a keyring that failed while reading may hold it, so the cloud setup is
    then offered and its check reports the keyring's error.
    """
    return status is not None and status.runpod_key_set
