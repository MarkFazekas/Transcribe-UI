"""`setup_cloud` and `cloud_setup_status`: the Runpod objects the worker needs (architecture §3.7, §5.4).

Stateless: nothing about the setup is stored locally; every request looks the objects up by
their fixed names (`runpod/inventory.py`). `setup_cloud` creates what is missing, rewrites the
secret values, updates an outdated template and removes duplicate secrets and templates. It
never deletes a Global Volume (it may hold backups): duplicates stop it with
`cloud_volume_duplicated`. A
create whose outcome is unknown stops the request; running it again adopts what was created.
Both requests are DB-free.
"""

from collections.abc import Callable
from functools import partial

from pydantic import SecretStr

from transcribe_ui.api.errors import DomainError
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runpod.access import api_failure, setup_credentials
from transcribe_ui.runpod.bodies import SecretCreate
from transcribe_ui.runpod.classes import (
    CloudObjectKind,
    CloudObjectOutcome,
    CloudSetupStatusRequest,
    CloudSetupStatusResponse,
    SetupCloudRequest,
    SetupCloudResponse,
)
from transcribe_ui.runpod.client import RunpodClient
from transcribe_ui.runpod.constants import (
    ERROR_CLOUD_OBJECT_IN_USE,
    ERROR_CLOUD_SETUP_INTERRUPTED,
    GLOBAL_VOLUME_NAME,
    HTTP_CLIENT_ERROR,
    HTTP_CONFLICT,
    TEMPLATE_NAME,
    WORKER_IMAGE,
)
from transcribe_ui.runpod.errors import AmbiguousCreateError, RunpodApiError
from transcribe_ui.runpod.inventory import (
    Inventory,
    desired_template,
    outdated_fields,
    read_statuses,
    volume_duplicated,
)
from transcribe_ui.runpod.wire import Template


def cloud_setup_status(_request: CloudSetupStatusRequest, context: RequestContext) -> CloudSetupStatusResponse:
    """Report the state of every object of the setup."""
    api_key = context.ports.credentials.runpod_api_key()
    with context.ports.runpod.client(api_key, context.cancel.is_cancelled) as client:
        statuses = read_statuses(client)
    ready = all(status.state == "ok" for status in statuses)
    return CloudSetupStatusResponse(ready=ready, worker_image=WORKER_IMAGE, cloud_objects=statuses)


def setup_cloud(_request: SetupCloudRequest, context: RequestContext) -> SetupCloudResponse:
    """Create or update the setup's objects; duplicate secrets and templates are removed."""
    api_key, secret_values = setup_credentials(context.ports.credentials)
    with context.ports.runpod.client(api_key, context.cancel.is_cancelled) as client:
        try:
            cloud_objects = _set_up(client, secret_values, context)
        except RunpodApiError as error:
            failure = api_failure(error)
        else:
            return SetupCloudResponse(cloud_objects=cloud_objects)
    # Raised outside the `except` block, so the Runpod error is not chained.
    raise failure


def _set_up(
    client: RunpodClient,
    secret_values: dict[str, SecretStr],
    context: RequestContext,
) -> tuple[CloudObjectOutcome, ...]:
    inventory = Inventory.read(client)
    context.check_cancelled()
    volume = _set_up_volume(client, inventory.volume_ids)
    context.check_cancelled()
    secrets = _set_up_secrets(client, inventory.secret_ids, secret_values)
    context.check_cancelled()
    template = _set_up_template(client, inventory.templates)
    return (volume, *secrets, template)


def _set_up_volume(client: RunpodClient, found: tuple[str, ...]) -> CloudObjectOutcome:
    kind: CloudObjectKind = "global_volume"
    if len(found) > 1:
        # Volumes may hold result backups: never deleted automatically (Q29).
        raise volume_duplicated(found)
    if found:
        return CloudObjectOutcome(kind=kind, name=GLOBAL_VOLUME_NAME, object_id=found[0], outcome="unchanged")
    created = _create(kind, GLOBAL_VOLUME_NAME, partial(client.volumes.create, GLOBAL_VOLUME_NAME))
    return CloudObjectOutcome(kind=kind, name=GLOBAL_VOLUME_NAME, object_id=created.volume_id, outcome="created")


def _set_up_secrets(
    client: RunpodClient,
    found: dict[str, tuple[str, ...]],
    secret_values: dict[str, SecretStr],
) -> list[CloudObjectOutcome]:
    outcomes = []
    for name, secret_value in secret_values.items():
        existing = found.get(name, ())
        if existing:
            keep, removed = _keep_first(existing, client.secrets.delete)
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
    keep, removed = _keep_first(ids, partial(_remove_template, client))
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
    try:
        return create()
    except AmbiguousCreateError:
        reason = "may_exist"
        message = (
            f"Creating the {_label(kind, name)} on Runpod failed after the request was sent, so it may exist "
            "already. Run the cloud setup again in a minute; it adopts what was created."
        )
    except RunpodApiError as error:
        if error.status != HTTP_CONFLICT:
            raise
        # Only secret names are unique: this one exists but is not listed yet.
        reason = "not_listed"
        message = (
            f"Runpod reports the {_label(kind, name)} exists but does not list it yet. "
            "Run the cloud setup again in a minute."
        )
    # Raised outside the `except` blocks, so the Runpod error is not chained.
    raise DomainError(ERROR_CLOUD_SETUP_INTERRUPTED, message, {"kind": kind, "name": name, "reason": reason})


def _keep_first(
    ids: tuple[str, ...],
    delete: Callable[[str], bool],
) -> tuple[str, int]:
    keep, *duplicates = ids
    for duplicate in duplicates:
        delete(duplicate)
    return keep, len(duplicates)


def _remove_template(client: RunpodClient, template_id: str) -> bool:
    try:
        return client.templates.delete(template_id)
    except RunpodApiError as error:
        # Runpod answers 400 for a template a pod or an endpoint still uses.
        if error.status != HTTP_CLIENT_ERROR:
            raise
    message = (
        f"A duplicate {_label('template', TEMPLATE_NAME)} ({template_id}) is in use and cannot be removed. "
        "Terminate the pods using it, then run the cloud setup again."
    )
    details = {"kind": "template", "name": TEMPLATE_NAME, "object_id": template_id}
    raise DomainError(ERROR_CLOUD_OBJECT_IN_USE, message, details)
