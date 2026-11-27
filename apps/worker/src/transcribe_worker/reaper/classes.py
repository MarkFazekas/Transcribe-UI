"""Pydantic models of the Runpod REST v2 list responses the reaper reads.

Only the fields the reaper needs are declared; everything else (including pod `env`, which
can hold secrets) is ignored and never logged.
"""

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


class ApiModel(BaseModel):
    """Base of the response models: immutable, unknown fields ignored."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class Pagination(ApiModel):
    """Cursor pagination block of every v2 list response."""

    next_cursor: str | None = Field(default=None, alias="nextCursor")
    has_next_page: bool = Field(alias="hasNextPage")


def _text_or_none(raw: object) -> str | None:
    return raw if isinstance(raw, str) else None


# A field kept only if it is a JSON string; any other value (or none) becomes None.
type LenientText = Annotated[str | None, BeforeValidator(_text_or_none)]


class ListedPod(ApiModel):
    """A pod or a queued pod as the list endpoints return it (id, name, status, createdAt).

    Parsed leniently: the list also holds foreign pods, and no value of these four fields
    (missing, null, a number, an object, ...) can fail the list; a non-string becomes None.
    Entries without a string name and status are foreign; the id and `createdAt` of app
    entries are validated per entry (`reap`).
    """

    pod_id: LenientText = Field(default=None, alias="id")
    name: LenientText = None
    status: LenientText = None
    created_at: LenientText = Field(default=None, alias="createdAt")


class Page(ApiModel):
    """One page of a list response."""

    entries: list[ListedPod]
    pagination: Pagination


class PodPage(Page):
    """One page of `GET /v2/pods`."""

    entries: list[ListedPod] = Field(alias="pods")


class QueuedPodPage(Page):
    """One page of `GET /v2/queued-pods`."""

    entries: list[ListedPod] = Field(alias="queuedPods")
