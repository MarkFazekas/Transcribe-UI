"""Account objects of the Runpod API.

Templates, secrets and Global Volumes (kept by the cloud setup), and the GPU catalog (prices).
"""

from pydantic import JsonValue, SecretStr

from transcribe_ui.runpod.bodies import SecretCreate, TemplateSpec
from transcribe_ui.runpod.calls import Endpoint
from transcribe_ui.runpod.constants import (
    GLOBAL_VOLUMES_PATH,
    GPU_CATALOG_PATH,
    SCOPE_CATALOG_READ,
    SCOPE_SECRETS_READ,
    SCOPE_SECRETS_WRITE,
    SCOPE_TEMPLATES_READ,
    SCOPE_TEMPLATES_WRITE,
    SCOPE_VOLUMES_READ,
    SCOPE_VOLUMES_WRITE,
    SECRETS_PATH,
    TEMPLATES_PATH,
)
from transcribe_ui.runpod.parsing import validate_entry
from transcribe_ui.runpod.session import RunpodHttp
from transcribe_ui.runpod.wire import (
    GlobalVolume,
    GlobalVolumePage,
    GpuCatalog,
    Secret,
    SecretPage,
    Template,
    TemplatePage,
)


def _named(entry: JsonValue, name: str) -> bool:
    return isinstance(entry, dict) and entry.get("name") == name


class TemplatesApi:
    """The account's templates."""

    def __init__(self, http: RunpodHttp) -> None:
        """Use this session."""
        self._http = http
        self._endpoint = Endpoint(TEMPLATES_PATH, "template", "templates", SCOPE_TEMPLATES_READ, SCOPE_TEMPLATES_WRITE)

    def named(self, name: str) -> tuple[Template, ...]:
        """Return the account's templates with exactly this name (others are never validated).

        Raises:
            RunpodApiError: A list failed, or a template of that name has an unexpected shape.
        """
        call = self._endpoint.listing()
        entries = self._http.collect(call, TemplatePage, lambda page: page.templates)
        found = [entry for entry in entries if _named(entry, name)]
        return tuple(validate_entry(call, entry, Template) for entry in found)

    def create(self, spec: TemplateSpec) -> Template:
        """Create a template.

        Raises:
            AmbiguousCreateError: The outcome is unknown.
        """
        return self._http.fetch_one(self._endpoint.create(spec.body()), Template)

    def update(self, template_id: str, spec: TemplateSpec) -> Template:
        """Set the template's fields to the spec (PATCH, idempotent)."""
        call = self._endpoint.update(template_id, spec.body())
        return self._http.fetch_one(call, Template)

    def delete(self, template_id: str) -> bool:
        """Delete a template (400 while a pod uses it); False if it does not exist (404)."""
        return self._http.send(self._endpoint.delete(template_id)) is not None


class SecretsApi:
    """The account's secrets (values are write-only)."""

    def __init__(self, http: RunpodHttp) -> None:
        """Use this session."""
        self._http = http
        self._endpoint = Endpoint(SECRETS_PATH, "secret", "secrets", SCOPE_SECRETS_READ, SCOPE_SECRETS_WRITE)

    def list_all(self) -> tuple[Secret, ...]:
        """Return every secret of the account (names, never values)."""
        call = self._endpoint.listing()
        return self._http.collect(call, SecretPage, lambda page: page.secrets)

    def create(self, secret: SecretCreate) -> Secret:
        """Create a secret (409 if the name is taken).

        Raises:
            AmbiguousCreateError: The outcome is unknown.
        """
        return self._http.fetch_one(self._endpoint.create(secret.body()), Secret)

    def set_value(self, secret_id: str, secret_value: SecretStr) -> Secret:
        """Replace the secret's value (PATCH, idempotent)."""
        body = {"value": secret_value.get_secret_value()}
        return self._http.fetch_one(self._endpoint.update(secret_id, body), Secret)

    def delete(self, secret_id: str) -> bool:
        """Delete a secret; False if it does not exist (404)."""
        return self._http.send(self._endpoint.delete(secret_id)) is not None


class VolumesApi:
    """The account's Global Volumes."""

    def __init__(self, http: RunpodHttp) -> None:
        """Use this session."""
        self._http = http
        self._endpoint = Endpoint(
            GLOBAL_VOLUMES_PATH,
            "global volume",
            "global volumes",
            SCOPE_VOLUMES_READ,
            SCOPE_VOLUMES_WRITE,
        )

    def list_all(self) -> tuple[GlobalVolume, ...]:
        """Return every Global Volume of the account."""
        call = self._endpoint.listing()
        return self._http.collect(call, GlobalVolumePage, lambda page: page.global_volumes)

    def create(self, name: str) -> GlobalVolume:
        """Create a Global Volume (names need not be unique).

        Raises:
            AmbiguousCreateError: The outcome is unknown.
        """
        return self._http.fetch_one(self._endpoint.create({"name": name}), GlobalVolume)

    def delete(self, volume_id: str) -> bool:
        """Delete a Global Volume (409 while mounted); False if it does not exist (404)."""
        return self._http.send(self._endpoint.delete(volume_id)) is not None


class CatalogApi:
    """The GPU catalog."""

    def __init__(self, http: RunpodHttp) -> None:
        """Use this session."""
        self._http = http
        self._endpoint = Endpoint(GPU_CATALOG_PATH, "gpu type", "gpu types", SCOPE_CATALOG_READ, SCOPE_CATALOG_READ)

    def secure_price(self, gpu_id: str) -> float | None:
        """Return the secure-cloud list price (USD/h, one GPU), or None if the type is not listed."""
        catalog = self._http.fetch_one(self._endpoint.listing(), GpuCatalog)
        prices = [gpu.price.secure for gpu in catalog.gpus if gpu.gpu_id == gpu_id]
        return prices[0] if prices else None
