"""Request and response models of the cloud setup, and the credentials it needs (architecture §3.6-3.7)."""

from typing import Literal

from pydantic import NonNegativeInt, SecretStr

from transcribe_ui.api.base import FrozenModel

# A cloud object the setup maintains by its fixed name.
type CloudObjectKind = Literal["global_volume", "secret", "template"]
# What `setup_cloud` did with an object.
type SetupOutcome = Literal["created", "updated", "unchanged"]
# How an object compares with what the app needs: fine, absent, different, or there several times.
type CloudObjectState = Literal["ok", "missing", "outdated", "duplicated"]
# Outcome of the Runpod key check.
type RunpodKeyState = Literal["ok", "missing", "rejected", "missing_scope", "unreachable"]


class R2Credentials(FrozenModel):
    """The R2 credentials the worker gets through Runpod secrets (from the desktop's credential store)."""

    endpoint_url: SecretStr
    bucket: SecretStr
    access_key_id: SecretStr
    secret_access_key: SecretStr


# Requests


class SetupCloudRequest(FrozenModel):
    """Create or update, by their fixed names, the Runpod objects the worker needs.

    The R2 secrets, the Global Volume and the worker template; duplicates by name are reduced to
    the oldest. Idempotent: run it again after an interruption or a credential rotation.
    """

    action: Literal["setup_cloud"]


class CloudSetupStatusRequest(FrozenModel):
    """Report which Runpod objects of the setup are missing, outdated or duplicated; changes nothing."""

    action: Literal["cloud_setup_status"]


# Responses


class CloudObjectOutcome(FrozenModel):
    """What `setup_cloud` did with one object."""

    kind: CloudObjectKind
    name: str
    # The id Runpod gave the object kept.
    object_id: str
    outcome: SetupOutcome
    duplicates_removed: NonNegativeInt = 0


class SetupCloudResponse(FrozenModel):
    """Every object of the setup, in setup order (volume, secrets, template)."""

    status: Literal["ok"] = "ok"
    action: Literal["setup_cloud"] = "setup_cloud"
    cloud_objects: tuple[CloudObjectOutcome, ...]


class CloudObjectStatus(FrozenModel):
    """One object of the setup as found on Runpod."""

    kind: CloudObjectKind
    name: str
    state: CloudObjectState
    # Id of the object `launch_worker` would use (the oldest of duplicates); None when missing.
    object_id: str | None = None
    # Fields that differ from what the app needs (names only, never values).
    outdated_fields: tuple[str, ...] = ()


class CloudSetupStatusResponse(FrozenModel):
    """Whether `launch_worker` can use the cloud setup as it is."""

    status: Literal["ok"] = "ok"
    action: Literal["cloud_setup_status"] = "cloud_setup_status"
    # True when every object is `ok`.
    ready: bool
    # The worker image the app needs the template to pin.
    worker_image: str
    cloud_objects: tuple[CloudObjectStatus, ...]


class RunpodKeyCheck(FrozenModel):
    """Whether the stored Runpod API key works (the Runpod half of `check_credentials`)."""

    state: RunpodKeyState
    message: str
    # The scope the key lacks, for `missing_scope`.
    scope: str | None = None
