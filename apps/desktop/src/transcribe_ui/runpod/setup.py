"""`setup_cloud` and `cloud_setup_status`: the Runpod objects the worker needs (architecture §3.7, §5.4).

Stateless: nothing about the setup is stored locally; every request looks the objects up by
their fixed names (`runpod/inventory.py`). `setup_cloud` creates what is missing, rewrites the
secret values, updates an outdated template and removes duplicates (keeping the oldest). A
create whose outcome is unknown stops the request; running it again adopts what was created.
Both requests are DB-free.
"""

from collections.abc import Callable
from functools import partial

from pydantic import SecretStr

from transcribe_ui.api.errors import DomainError
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runpod.access import RunpodAccess, api_failure, missing_credentials
from transcribe_ui.runpod.bodies import SecretCreate
from transcribe_ui.runpod.classes import (
    CloudObjectKind,
    CloudObjectOutcome,
    CloudSetupStatusRequest,
    CloudSetupStatusResponse,
    R2Credentials,
    SetupCloudRequest,
    SetupCloudResponse,
)
from transcribe_ui.runpod.client import RunpodClient
from transcribe_ui.runpod.constants import (
    CREDENTIAL_R2,
    CREDENTIAL_RUNPOD_API_KEY,
    ERROR_CLOUD_OBJECT_IN_USE,
    ERROR_CLOUD_SETUP_INTERRUPTED,
    GLOBAL_VOLUME_NAME,
    HTTP_CLIENT_ERROR,
    HTTP_CONFLICT,
    SECRET_FOR_ENV,
    TEMPLATE_NAME,
    WORKER_IMAGE,
)
from transcribe_ui.runpod.errors import AmbiguousCreateError, RunpodApiError
from transcribe_ui.runpod.inventory import Inventory, desired_template, outdated_fields, read_statuses
from transcribe_ui.runpod.wire import Template


def cloud_setup_status(_request: CloudSetupStatusRequest, context: RequestContext) -> CloudSetupStatusResponse:
    """Report the state of every object of the setup."""
    with context.ports.runpod.client() as client:
        statuses = read_statuses(client)
    ready = all(status.state == "ok" for status in statuses)
    return CloudSetupStatusResponse(ready=ready, worker_image=WORKER_IMAGE, cloud_objects=statuses)


def setup_cloud(_request: SetupCloudRequest, context: RequestContext) -> SetupCloudResponse:
    """Create or update the setup's objects; duplicates by name are reduced to the oldest."""
    access = context.ports.runpod
    r2 = _require_credentials(access)
    with access.client() as client:
        try:
            return SetupCloudResponse(cloud_objects=_set_up(client, r2, context))
        except RunpodApiError as error:
            raise api_failure(error) from None


def _require_credentials(access: RunpodAccess) -> R2Credentials:
    r2 = access.credentials.r2_credentials()
    missing = [CREDENTIAL_RUNPOD_API_KEY] if access.credentials.runpod_api_key() is None else []
    if r2 is None:
        missing.append(CREDENTIAL_R2)
    if r2 is None or missing:
        raise missing_credentials(missing)
    return r2


def _set_up(client: RunpodClient, r2: R2Credentials, context: RequestContext) -> tuple[CloudObjectOutcome, ...]:
    inventory = Inventory.read(client)
    context.check_cancelled()
    volume = _set_up_volume(client, inventory.volume_ids)
    context.check_cancelled()
    secrets = _set_up_secrets(client, inventory.secret_ids, r2)
    context.check_cancelled()
    template = _set_up_template(client, inventory.templates)
    return (volume, *secrets, template)


def _set_up_volume(client: RunpodClient, found: tuple[str, ...]) -> CloudObjectOutcome:
    kind: CloudObjectKind = "global_volume"
    if not found:
        created = _create(kind, GLOBAL_VOLUME_NAME, partial(client.volumes.create, GLOBAL_VOLUME_NAME))
        return CloudObjectOutcome(kind=kind, name=GLOBAL_VOLUME_NAME, object_id=created.volume_id, outcome="created")
    keep, removed = _keep_oldest(kind, GLOBAL_VOLUME_NAME, found, client.volumes.delete)
    return CloudObjectOutcome(
        kind=kind,
        name=GLOBAL_VOLUME_NAME,
        object_id=keep,
        outcome="unchanged",
        duplicates_removed=removed,
    )


def _secret_values(r2: R2Credentials) -> dict[str, SecretStr]:
    parts = (r2.endpoint_url, r2.bucket, r2.access_key_id, r2.secret_access_key)
    names = [secret for _, secret in SECRET_FOR_ENV]
    return dict(zip(names, parts, strict=True))


def _set_up_secrets(
    client: RunpodClient,
    found: dict[str, tuple[str, ...]],
    r2: R2Credentials,
) -> list[CloudObjectOutcome]:
    outcomes = []
    for name, secret_value in _secret_values(r2).items():
        existing = found.get(name, ())
        if existing:
            keep, removed = _keep_oldest("secret", name, existing, client.secrets.delete)
            # Values are write-only, so whether one changed is unknown: always rewrite it.
            client.secrets.set_value(keep, secret_value)
            outcome = CloudObjectOutcome(
                kind="secret",
                name=name,
                object_id=keep,
                outcome="updated",
                duplicates_removed=removed,
            )
        else:
            body = SecretCreate(name=name, secret_value=secret_value)
            created = _create("secret", name, partial(client.secrets.create, body))
            outcome = CloudObjectOutcome(kind="secret", name=name, object_id=created.secret_id, outcome="created")
        outcomes.append(outcome)
    return outcomes


def _set_up_template(client: RunpodClient, found: tuple[Template, ...]) -> CloudObjectOutcome:
    kind: CloudObjectKind = "template"
    wanted = desired_template()
    if not found:
        created = _create(kind, TEMPLATE_NAME, partial(client.templates.create, wanted))
        return CloudObjectOutcome(kind=kind, name=TEMPLATE_NAME, object_id=created.template_id, outcome="created")
    ids = tuple(template.template_id for template in found)
    keep, removed = _keep_oldest(kind, TEMPLATE_NAME, ids, client.templates.delete)
    stale = bool(outdated_fields(found[0], wanted))
    if stale:
        client.templates.update(keep, wanted)
    return CloudObjectOutcome(
        kind=kind,
        name=TEMPLATE_NAME,
        object_id=keep,
        outcome="updated" if stale else "unchanged",
        duplicates_removed=removed,
    )


def _label(kind: CloudObjectKind, name: str) -> str:
    return f"{kind.replace('_', ' ')} {name!r}"


def _create[CreatedT](kind: CloudObjectKind, name: str, create: Callable[[], CreatedT]) -> CreatedT:
    details = {"kind": kind, "name": name}
    try:
        return create()
    except AmbiguousCreateError:
        message = (
            f"Creating the {_label(kind, name)} on Runpod failed after the request was sent, so it may exist "
            "already. Run the cloud setup again in a minute; it adopts what was created."
        )
        raise DomainError(ERROR_CLOUD_SETUP_INTERRUPTED, message, details) from None
    except RunpodApiError as error:
        if error.status != HTTP_CONFLICT:
            raise
        # Only secret names are unique: this one exists but is not listed yet.
        message = (
            f"Runpod reports the {_label(kind, name)} exists but does not list it yet. "
            "Run the cloud setup again in a minute."
        )
        raise DomainError(ERROR_CLOUD_SETUP_INTERRUPTED, message, details) from None


def _keep_oldest(
    kind: CloudObjectKind,
    name: str,
    ids: tuple[str, ...],
    delete: Callable[[str], bool],
) -> tuple[str, int]:
    keep, *duplicates = ids
    for duplicate in duplicates:
        _remove(kind, name, duplicate, delete)
    return keep, len(duplicates)


def _remove(
    kind: CloudObjectKind,
    name: str,
    object_id: str,
    delete: Callable[[str], bool],
) -> None:
    try:
        delete(object_id)
    except RunpodApiError as error:
        # A template in use answers 400, a mounted Global Volume 409.
        if error.status not in {HTTP_CLIENT_ERROR, HTTP_CONFLICT}:
            raise
        message = (
            f"A duplicate {_label(kind, name)} ({object_id}) is in use and cannot be removed. "
            "Terminate the pods using it, then run the cloud setup again."
        )
        details = {"kind": kind, "name": name, "object_id": object_id}
        raise DomainError(ERROR_CLOUD_OBJECT_IN_USE, message, details) from None
