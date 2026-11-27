"""Request and response models of the backend façade (architecture §3.1).

Every frontend builds a `Request`, hands it to `Backend.execute` and renders the `Response` and
the `ProgressEvent`s. A response is one of: the request's own success model (`status` "ok"),
`NeedsConfirmation` (nothing was changed; send the request again with the confirmation field
set) or `ErrorResponse`.
"""

from typing import Annotated, Literal

from pydantic import Field, JsonValue, NonNegativeInt

from transcribe_ui.api.base import FrozenModel, LocalPath
from transcribe_ui.discovery.classes import (
    PreviewSelectionRequest,
    PreviewSelectionResponse,
    ScanTreeRequest,
    ScanTreeResponse,
)

# Requests


class GetAppInfoRequest(FrozenModel):
    """Ask for the app version and the data contract version (nothing machine-specific)."""

    # Request actions have no default, so the schema marks them required.
    action: Literal["get_app_info"]


# Discriminated on `action`; each part adds its request models here (`A | B | ...`).
type Request = Annotated[
    GetAppInfoRequest | ScanTreeRequest | PreviewSelectionRequest,
    Field(discriminator="action"),
]


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
type SuccessResponse = Annotated[
    GetAppInfoResponse | ScanTreeResponse | PreviewSelectionResponse,
    Field(discriminator="action"),
]
# Every response of `Backend.execute`, discriminated on `status`.
type Response = Annotated[SuccessResponse | ErrorResponse | NeedsConfirmation, Field(discriminator="status")]


class ProgressEvent(FrozenModel):
    """Progress of a long request; the CLI writes these to stderr as NDJSON."""

    action: str
    stage: str
    completed: NonNegativeInt
    total: NonNegativeInt | None = None
    path: LocalPath | None = None
