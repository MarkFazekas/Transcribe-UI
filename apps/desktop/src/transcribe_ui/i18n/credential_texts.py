"""The texts of the credentials a user types: labels and rules (CLI `credentials`, GUI Accounts page)."""

from transcribe_ui.credentials.classes import CredentialName
from transcribe_ui.credentials.constants import (
    R2_ACCESS_KEY_ID,
    R2_BUCKET,
    R2_ENDPOINT_URL,
    R2_SECRET_ACCESS_KEY,
    RUNPOD_API_KEY,
    TOKEN_MAX_LENGTH,
)
from transcribe_ui.i18n.catalog import tr


def credential_labels() -> dict[CredentialName, str]:
    """Return the prompt label of each credential in the active language."""
    return {
        R2_ENDPOINT_URL: tr("R2 endpoint URL"),
        R2_BUCKET: tr("R2 bucket name"),
        R2_ACCESS_KEY_ID: tr("R2 access key ID"),
        R2_SECRET_ACCESS_KEY: tr("R2 secret access key"),
        RUNPOD_API_KEY: tr("Runpod API key"),
    }


def credential_rules() -> dict[CredentialName, str]:
    """Return what each credential must look like, shown when an answer is refused.

    The rules are those of `credentials/classes.py` (a test ties the English texts to its
    messages); the texts never contain the answer.
    """
    key_rule = tr(
        "A key must be ASCII without spaces or control characters, at most {max_length} characters,"
        " and not only '*' characters.",
    ).format(max_length=TOKEN_MAX_LENGTH)
    return {
        R2_ENDPOINT_URL: tr(
            "The endpoint URL must be an https URL with a host name only (https://<account>.r2.cloudflarestorage.com).",
        ),
        R2_BUCKET: tr(
            "The bucket name must be 3-63 lower-case letters, digits and hyphens, not starting or"
            " ending with a hyphen.",
        ),
        R2_ACCESS_KEY_ID: key_rule,
        R2_SECRET_ACCESS_KEY: key_rule,
        RUNPOD_API_KEY: key_rule,
    }
