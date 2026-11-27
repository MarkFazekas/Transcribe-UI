"""Pydantic models of the Runpod REST v2 bodies the client reads.

Read models keep only the fields the app uses and ignore the rest; above all, a pod's `env`
(which holds the R2 secrets and a pod-scoped API key) is never parsed, so it cannot reach a
response, a log line or an error message. Template env values are kept as `SecretStr`, so a
template someone edited by hand to hold a plaintext value cannot leak either.
"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, SecretStr, alias_generators

from transcribe_ui.runpod.constants import ID_FIELD


class ApiModel(BaseModel):
    """A body read from the API: camelCase on the wire, unknown fields dropped."""

    model_config = ConfigDict(
        frozen=True, extra="ignore", alias_generator=alias_generators.to_camel, validate_by_name=True
    )


class Pagination(ApiModel):
    """Cursor of a paginated list."""

    next_cursor: str | None = None
    has_next_page: bool = False


class Paged(ApiModel):
    """A page of a list response: its entries (in the subclass) plus the cursor."""

    pagination: Pagination = Field(default_factory=Pagination)


class Pod(ApiModel):
    """A pod, without its `env`."""

    pod_id: str = Field(alias=ID_FIELD)
    name: str
    # PROVISIONING, STARTING, RUNNING, EXITED, ERROR or TERMINATED; kept as text, so a new
    # status does not make the whole list unreadable.
    status: str
    created_at: AwareDatetime | None = None
    started_at: AwareDatetime | None = None
    # Price in dollars per hour.
    cost: float | None = None
    # Id of the template the pod was created from.
    template: str | None = None


class QueueWindow(ApiModel):
    """When a queued pod waits for capacity."""

    starts_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None


class QueuedPod(ApiModel):
    """A queued pod ("deploy when available"), listed whatever its status."""

    queued_pod_id: str = Field(alias=ID_FIELD)
    name: str
    # QUEUED, DEPLOYED, FAILED, EXPIRED or CANCELLED.
    status: str
    # The created pod's id; null until it deployed.
    pod_id: str | None = None
    failure_reason: str | None = None
    created_at: AwareDatetime | None = None
    resolved_at: AwareDatetime | None = None
    queue: QueueWindow | None = None


class Template(ApiModel):
    """A template of the account (templates carry no creation time)."""

    template_id: str = Field(alias=ID_FIELD)
    name: str
    image: str
    env: dict[str, SecretStr] = Field(default_factory=dict)
    start_ssh: bool = True
    start_jupyter: bool = True


class Secret(ApiModel):
    """A secret of the account; its value is write-only and never returned."""

    secret_id: str = Field(alias=ID_FIELD)
    name: str
    created_at: AwareDatetime


class GlobalVolume(ApiModel):
    """A Global Volume (names need not be unique)."""

    volume_id: str = Field(alias=ID_FIELD)
    name: str
    created_at: AwareDatetime


class GpuPrice(ApiModel):
    """List prices in dollars per hour for one GPU."""

    secure: float | None = None
    community: float | None = None


class GpuType(ApiModel):
    """A GPU type of the catalog."""

    gpu_id: str = Field(alias=ID_FIELD)
    price: GpuPrice = Field(default_factory=GpuPrice)


class PodPage(Paged):
    """One page of `GET /pods`."""

    pods: tuple[Pod, ...]


class QueuedPodPage(Paged):
    """One page of `GET /queued-pods`."""

    queued_pods: tuple[QueuedPod, ...]


class TemplatePage(Paged):
    """One page of `GET /templates`; entries stay raw until filtered by name.

    The account may hold any template, so only the app's own are validated strictly
    (`TemplatesApi.named`): an unrelated template of an unexpected shape cannot fail the list.
    """

    templates: tuple[JsonValue, ...]


class SecretPage(Paged):
    """`GET /account/secrets` (not paginated)."""

    secrets: tuple[Secret, ...]


class GlobalVolumePage(Paged):
    """One page of `GET /global-volumes`."""

    global_volumes: tuple[GlobalVolume, ...]


class GpuCatalog(Paged):
    """`GET /catalog/gpus` (not paginated)."""

    gpus: tuple[GpuType, ...]
