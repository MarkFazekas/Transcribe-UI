"""Pydantic models of the request bodies the Runpod client sends (REST v2)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr, alias_generators


class SendModel(BaseModel):
    """A request body: camelCase on the wire, written without unset (None) fields."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", alias_generator=alias_generators.to_camel, validate_by_name=True
    )

    def body(self) -> dict[str, object]:
        """Return the JSON body."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class TemplateSpec(SendModel):
    """The worker template as the cloud setup wants it (the body of both create and update)."""

    name: str
    image: str
    # Non-secret values and `{{ RUNPOD_SECRET_<name> }}` references only, never secret values.
    env: dict[str, str]
    start_ssh: bool = False
    start_jupyter: bool = False


class SecretCreate(SendModel):
    """Body of `POST /account/secrets`."""

    name: str
    secret_value: SecretStr = Field(alias="value")

    def body(self) -> dict[str, object]:
        """Return the JSON body with the secret's value in clear (sent only to Runpod)."""
        return {"name": self.name, "value": self.secret_value.get_secret_value()}


class GpuRequest(SendModel):
    """GPU of a pod create body."""

    gpu_id: str = Field(alias="id")
    count: int = 1
    min_cuda_version: str | None = None


class GlobalMount(SendModel):
    """A Global Volume mount of a pod create body."""

    volume_id: str
    path: str | None = None


class Mounts(SendModel):
    """Mounts of a pod create body (only the Global Volume is used)."""

    global_: tuple[GlobalMount, ...] = Field(alias="global")


class QueueRequest(SendModel):
    """`queue` of a pod create body: deploy when available, until `expiresAt`."""

    expires_at: datetime


class CreatePod(SendModel):
    """Body of `POST /pods` (architecture §3.7; built by `launch_worker`)."""

    name: str
    template_id: str
    gpu: GpuRequest
    cloud: str
    disk: int
    mounts: Mounts
    # Non-secret values only (run id, runtime, test mode).
    env: dict[str, str]
    queue: QueueRequest | None = None
