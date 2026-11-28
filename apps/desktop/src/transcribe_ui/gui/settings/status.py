"""What the credentials' status means for the first start and the cloud setup (Qt-free; architecture §3.9)."""

from transcribe_ui.credentials.classes import CredentialsStatusResponse


def r2_missing(status: CredentialsStatusResponse) -> bool:
    """Return whether the backend counts the R2 credentials as not all set (the first-start wizard opens then).

    The backend's rule (`r2_set`): a keyring that failed while reading may hold them.
    """
    return not status.r2_set


def runpod_key_set(status: CredentialsStatusResponse | None) -> bool:
    """Return whether the backend counts the Runpod API key as set (the cloud setup needs it, §3.9).

    The backend's rule: a keyring that failed while reading may hold it, so the cloud setup is
    then offered and its check reports the keyring's error.
    """
    return status is not None and status.runpod_key_set
