"""Request and response models of the backend façade (architecture §3.1).

Every frontend builds a `Request`, hands it to `Backend.execute` and renders the `Response` and
the `ProgressEvent`s. A response is one of: the request's own success model (`status` "ok"),
`NeedsConfirmation` (nothing was changed; send the request again with the confirmation field
set) or `ErrorResponse`.
"""

from collections.abc import Callable, Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, NonNegativeInt, PlainSerializer, SerializationInfo

from transcribe_contracts.classes import PathFlavor as ContractPathFlavor
from transcribe_ui.api.constants import LOCAL_PATH_CONTEXT_KEY, PATH_FLAVOR_CONTEXT_KEY


def _rewritten(text: str, serialization: SerializationInfo, key: str) -> str:
    """Return the text passed through the rewrite function the serialization context holds."""
    context = serialization.context
    if not isinstance(context, Mapping):
        return text
    rewrite = context.get(key)
    if not callable(rewrite):
        return text
    return str(rewrite(text))


def _serialise_local_path(path: str, serialization: SerializationInfo) -> str:
    return _rewritten(path, serialization, LOCAL_PATH_CONTEXT_KEY)


def _serialise_path_flavor(flavor: str, serialization: SerializationInfo) -> str:
    return _rewritten(flavor, serialization, PATH_FLAVOR_CONTEXT_KEY)


# A path on this computer, as the OS reports it. The type marks the field for the golden-file
# normaliser, which passes a rewrite function in the serialization context; without one the
# value is written unchanged.
type LocalPath = Annotated[str, PlainSerializer(_serialise_local_path, return_type=str)]
# The path flavour of this computer; marked for the golden-file normaliser like `LocalPath`.
type PathFlavor = Annotated[ContractPathFlavor, PlainSerializer(_serialise_path_flavor, return_type=str)]

# Signature of a serialization rewrite function (see `LocalPath`).
type Rewrite = Callable[[str], str]


class FrozenModel(BaseModel):
    """Base of the façade models: immutable, unknown fields refused.

    In the responses' (serialization) schema, fields with defaults are required: they are
    always written.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", json_schema_serialization_defaults_required=True)


# Requests


class GetAppInfoRequest(FrozenModel):
    """Ask for the app version and the data contract version (nothing machine-specific)."""

    # Request actions have no default, so the schema marks them required.
    action: Literal["get_app_info"]


# Discriminated on `action`; each part adds its request models here (`A | B | ...`).
type Request = Annotated[GetAppInfoRequest, Field(discriminator="action")]


# Responses


class GetAppInfoResponse(FrozenModel):
    """Versions of the running app."""

    status: Literal["ok"] = "ok"
    action: Literal["get_app_info"] = "get_app_info"
    app_version: str
    contracts_schema_version: str


class ErrorBody(FrozenModel):
    """What went wrong: a stable code, a human-readable message and structured details."""

    code: str
    message: str
    details: dict[str, JsonValue] = Field(default_factory=dict)


class ErrorResponse(FrozenModel):
    """The request failed; for most codes nothing was changed (see the code's documentation)."""

    status: Literal["error"] = "error"
    # None when the request document could not be read or validated.
    action: str | None
    error: ErrorBody


class Confirmation(FrozenModel):
    """One thing the user has to confirm before the request is carried out."""

    # Request field that confirms it when set.
    field: str
    message: str
    details: dict[str, JsonValue] = Field(default_factory=dict)


class NeedsConfirmation(FrozenModel):
    """Nothing was changed: send the same request again with the listed fields set."""

    status: Literal["needs_confirmation"] = "needs_confirmation"
    action: str
    confirmations: tuple[Confirmation, ...] = Field(min_length=1)


# Discriminated on `action`; each part adds its success response models here.
type SuccessResponse = Annotated[GetAppInfoResponse, Field(discriminator="action")]
# Every response of `Backend.execute`, discriminated on `status`.
type Response = Annotated[SuccessResponse | ErrorResponse | NeedsConfirmation, Field(discriminator="status")]


class ProgressEvent(FrozenModel):
    """Progress of a long request; the CLI writes these to stderr as NDJSON."""

    action: str
    stage: str
    completed: NonNegativeInt
    total: NonNegativeInt | None = None
    path: LocalPath | None = None
