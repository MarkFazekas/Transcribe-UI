"""Texts of the accounts, the cloud setup and the first start (architecture §3.1 "Language of the texts").

Qt-free. Each text is translated when it is shown (`tr`), never at import time.
"""

from transcribe_ui.credentials.classes import (
    CheckedService,
    CheckOutcome,
    CredentialStatus,
    KeyringState,
)
from transcribe_ui.gui.settings.constants import NoticeKind
from transcribe_ui.i18n.catalog import ntr, tr
from transcribe_ui.runpod.classes import CloudObjectKind, CloudObjectStatus, SetupCloudResponse


def credential_label(name: str) -> str:
    """Return the label of a credential's field (by credential name)."""
    labels = {
        "r2_endpoint_url": tr("Endpoint URL"),
        "r2_bucket": tr("Bucket"),
        "r2_access_key_id": tr("Access key ID"),
        "r2_secret_access_key": tr("Secret access key"),
        "runpod_api_key": tr("API key"),
    }
    return labels[name]


def credential_source(credential: CredentialStatus) -> str:
    """Return where a credential is set (never its value)."""
    if credential.source == "keyring":
        return tr("Stored in the keyring")
    if credential.source == "environment":
        return tr("From the environment variable {variable}").format(variable=credential.environment_variable)
    return tr("Not set")


def credential_placeholder(credential: CredentialStatus) -> str:
    """Return the hint shown in a credential's empty field."""
    if credential.source == "missing":
        return tr("Enter the value")
    return tr("Type a new value to replace it")


def credential_hint(name: str) -> str:
    """Return how to fix a credential the request's validator refused (by field key)."""
    hints = {
        "r2_endpoint_url": tr(
            "Enter the S3 API endpoint of R2 without a path: https://<account ID>.r2.cloudflarestorage.com",
        ),
        "r2_bucket": tr("Use 3-63 lower-case letters, digits and hyphens, not starting or ending with a hyphen."),
    }
    token = tr("Paste the whole value: no spaces, at most 1024 characters, not the masked one (*****).")
    return hints.get(name, token)


def keyring_note(keyring: KeyringState) -> str:
    """Return what the accounts page says about the keyring ("" when it is usable)."""
    if keyring == "unavailable":
        return tr(
            "This system has no keyring, so the values cannot be stored here. Set the environment variables "
            "instead, then restart the app.",
        )
    if keyring == "failed":
        return tr(
            "The keyring cannot be used right now (it may be locked). Unlock it, or use the environment variables."
        )
    return ""


def runpod_optional_note() -> str:
    """Return the first-run note on the optional Runpod key (R2-only role, architecture §3.9)."""
    return tr(
        "The Runpod API key is optional. Without it, this computer prepares, uploads and downloads jobs, and "
        "another computer that has the key starts the transcription.",
    )


def service_label(service: CheckedService) -> str:
    """Return the name of a checked service."""
    return {"r2": tr("R2 storage"), "runpod": tr("Runpod")}[service]


def outcome_label(outcome: CheckOutcome) -> str:
    """Return the short result of a credential check."""
    labels: dict[CheckOutcome, str] = {
        "ok": tr("OK"),
        "missing": tr("Not set"),
        "failed": tr("Failed"),
    }
    return labels[outcome]


def object_kind(kind: CloudObjectKind) -> str:
    """Return the name of a kind of cloud object."""
    kinds: dict[CloudObjectKind, str] = {
        "global_volume": tr("Backup volume"),
        "secret": tr("Secret"),
        "template": tr("Worker template"),
    }
    return kinds[kind]


def object_state(cloud_object: CloudObjectStatus) -> str:
    """Return how a cloud object compares with what the app needs."""
    if cloud_object.state == "outdated":
        return tr("Outdated ({fields})").format(fields=", ".join(cloud_object.outdated_fields))
    states = {
        "ok": tr("Ready"),
        "missing": tr("Missing"),
        "duplicated": tr("Duplicated"),
    }
    return states[cloud_object.state]


def setup_summary(response: SetupCloudResponse) -> str:
    """Return what `setup_cloud` did, as one line."""
    outcomes = [cloud_object.outcome for cloud_object in response.cloud_objects]
    removed = sum(cloud_object.duplicates_removed for cloud_object in response.cloud_objects)
    summary = tr("Cloud setup done: {created} created, {updated} updated, {unchanged} unchanged.").format(
        created=outcomes.count("created"),
        updated=outcomes.count("updated"),
        unchanged=outcomes.count("unchanged"),
    )
    if not removed:
        return summary
    duplicates = ntr("{count} duplicate removed.", "{count} duplicates removed.", removed).format(count=removed)
    return f"{summary} {duplicates}"


def notice_text(notice: NoticeKind, error: str) -> str:
    """Return the window's start-up notice (`error`: the failed cloud check's text)."""
    if notice == "accounts":
        start = tr("Enter the R2 storage values under Settings, Accounts to start.")
        return f"{start} {runpod_optional_note()}"
    if notice == "cloud_setup":
        return tr("The cloud setup is incomplete or outdated, so transcription cannot start from this computer yet.")
    return tr("The cloud setup could not be checked: {error}").format(error=error)
