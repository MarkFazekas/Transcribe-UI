"""Pydantic models of the Runpod REST v2 list responses the reaper reads.

Only the fields the reaper needs are declared; everything else (including pod `env`, which
can hold secrets) is ignored and never logged.
"""

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    """Base of the response models: immutable, unknown fields ignored."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class Pagination(ApiModel):
    """Cursor pagination block of every v2 list response."""

    next_cursor: str | None = Field(default=None, alias="nextCursor")
    has_next_page: bool = Field(alias="hasNextPage")


class ListedPod(ApiModel):
    """A pod or a queued pod as the list endpoints return it (id, name, status, createdAt).

    Parsed leniently: the list also holds foreign pods, and one odd entry must not fail the
    whole list. Id and `createdAt` of app entries are validated per entry (`reap`).
    """

    pod_id: str = Field(alias="id")
    name: str
    status: str
    created_at: str | None = Field(default=None, alias="createdAt")


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
