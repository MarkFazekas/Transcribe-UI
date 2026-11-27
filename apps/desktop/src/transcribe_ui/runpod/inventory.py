"""The cloud setup's objects as found on Runpod, and how they compare with what the app needs.

Objects are found by their fixed names. Several objects with one name are ordered oldest first
(`createdAt`, then id); templates carry no creation time, so for them the lowest id stands in.
`launch_worker` uses the first of each and refuses while any object is missing, outdated or
duplicated (`require_cloud_setup`).
"""

from dataclasses import dataclass
from functools import partial
from itertools import starmap
from typing import Self

from transcribe_ui.api.errors import DomainError
from transcribe_ui.runpod.access import api_failure
from transcribe_ui.runpod.bodies import TemplateSpec
from transcribe_ui.runpod.classes import CloudObjectKind, CloudObjectStatus
from transcribe_ui.runpod.client import RunpodClient
from transcribe_ui.runpod.constants import (
    ENV_REFERENCE_FORMAT,
    ERROR_CLOUD_NOT_SET_UP,
    ERROR_CLOUD_SETUP_OUTDATED,
    GLOBAL_VOLUME_NAME,
    SECRET_FOR_ENV,
    TEMPLATE_NAME,
    WORKER_IMAGE,
)
from transcribe_ui.runpod.errors import RunpodApiError
from transcribe_ui.runpod.wire import GlobalVolume, Secret, Template


@dataclass(frozen=True)
class CloudSetup:
    """The objects `launch_worker` uses."""

    template_id: str
    global_volume_id: str


def desired_template() -> TemplateSpec:
    """Return the worker template the app needs.

    The image of the app's contracts major, no SSH or Jupyter, and the R2 credentials as
    secret references (never values).
    """
    env = {env_name: ENV_REFERENCE_FORMAT.format(name=secret) for env_name, secret in SECRET_FOR_ENV}
    return TemplateSpec(name=TEMPLATE_NAME, image=WORKER_IMAGE, env=env)


def _env_matches(template: Template, wanted: TemplateSpec) -> bool:
    # Env entries the app does not set are ignored.
    for env_name, reference in wanted.env.items():
        found = template.env.get(env_name)
        if found is None or found.get_secret_value() != reference:
            return False
    return True


def outdated_fields(template: Template, wanted: TemplateSpec) -> tuple[str, ...]:
    """Return the fields of the template that differ from the wanted one (names only).

    Env entries the app does not set are ignored, so it does not matter whether Runpod merges
    or replaces `env` on update.
    """
    checks = (
        ("image", template.image != wanted.image),
        ("env", not _env_matches(template, wanted)),
        ("start_ssh", template.start_ssh != wanted.start_ssh),
        ("start_jupyter", template.start_jupyter != wanted.start_jupyter),
    )
    return tuple(name for name, differs in checks if differs)


def _same_secret_name(left: str, right: str) -> bool:
    # Runpod matches secret names case-insensitively.
    return left.casefold() == right.casefold()


def _volume_ids(volumes: tuple[GlobalVolume, ...]) -> tuple[str, ...]:
    named = [volume for volume in volumes if volume.name == GLOBAL_VOLUME_NAME]
    named.sort(key=lambda volume: (volume.created_at, volume.volume_id))
    return tuple(volume.volume_id for volume in named)


def _secret_ids(secrets: tuple[Secret, ...], name: str) -> tuple[str, ...]:
    named = [secret for secret in secrets if _same_secret_name(secret.name, name)]
    named.sort(key=lambda secret: (secret.created_at, secret.secret_id))
    return tuple(secret.secret_id for secret in named)


def _templates(templates: tuple[Template, ...]) -> tuple[Template, ...]:
    # Templates carry no creation time: the lowest id stands in for the oldest.
    named = [template for template in templates if template.name == TEMPLATE_NAME]
    named.sort(key=lambda template: template.template_id)
    return tuple(named)


@dataclass(frozen=True)
class Inventory:
    """Ids of the setup's objects, oldest first per name."""

    volume_ids: tuple[str, ...]
    secret_ids: dict[str, tuple[str, ...]]
    templates: tuple[Template, ...]

    @classmethod
    def read(cls, client: RunpodClient) -> Self:
        """List the setup's objects by name.

        Raises:
            RunpodApiError: A list failed.
        """
        listed = client.secrets.list_all()
        secret_ids = {name: _secret_ids(listed, name) for _, name in SECRET_FOR_ENV}
        volume_ids = _volume_ids(client.volumes.list_all())
        return cls(volume_ids, secret_ids, _templates(client.templates.list_all()))

    def statuses(self) -> tuple[CloudObjectStatus, ...]:
        """Return every object's state, in setup order (volume, secrets, template)."""
        volume = _status("global_volume", GLOBAL_VOLUME_NAME, self.volume_ids)
        secrets = starmap(partial(_status, "secret"), self.secret_ids.items())
        template_ids = tuple(template.template_id for template in self.templates)
        template = _status("template", TEMPLATE_NAME, template_ids)
        if template.state == "ok":
            fields = outdated_fields(self.templates[0], desired_template())
            if fields:
                template = template.model_copy(update={"state": "outdated", "outdated_fields": fields})
        return (volume, *secrets, template)


def _status(kind: CloudObjectKind, name: str, ids: tuple[str, ...]) -> CloudObjectStatus:
    if not ids:
        return CloudObjectStatus(kind=kind, name=name, state="missing")
    state = "duplicated" if len(ids) > 1 else "ok"
    return CloudObjectStatus(kind=kind, name=name, state=state, object_id=ids[0])


def read_statuses(client: RunpodClient) -> tuple[CloudObjectStatus, ...]:
    """Return every object's state.

    Raises:
        DomainError: The error of a failed Runpod call.
    """
    try:
        return Inventory.read(client).statuses()
    except RunpodApiError as error:
        raise api_failure(error) from None


def require_cloud_setup(client: RunpodClient) -> CloudSetup:
    """Return the template and Global Volume `launch_worker` uses.

    Raises:
        DomainError: `cloud_not_set_up` if an object is missing, `cloud_setup_outdated` if one
            is outdated or duplicated, or the error of a failed Runpod call.
    """
    statuses = read_statuses(client)
    missing = [status.name for status in statuses if status.state == "missing"]
    if missing:
        message = "The Runpod cloud setup is incomplete. Run the cloud setup first."
        raise DomainError(ERROR_CLOUD_NOT_SET_UP, message, {"missing": list(missing)})
    stale = [status.name for status in statuses if status.state != "ok"]
    if stale:
        message = "The Runpod cloud setup is outdated. Run the cloud setup again."
        raise DomainError(ERROR_CLOUD_SETUP_OUTDATED, message, {"outdated": list(stale)})
    ids = {status.kind: status.object_id or "" for status in statuses}
    return CloudSetup(template_id=ids["template"], global_volume_id=ids["global_volume"])
