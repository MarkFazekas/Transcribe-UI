"""Pydantic models of the Runpod REST v2 list responses the reaper reads.

Only the fields the reaper needs are declared; everything else (including pod `env`, which
can hold secrets) is ignored and never logged.
"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from transcribe_worker.reaper.constants import ITEM_ID_PATTERN


class ApiModel(BaseModel):
    """Base of the response models: immutable, unknown fields ignored."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class Pagination(ApiModel):
    """Cursor pagination block of every v2 list response."""

    next_cursor: str | None = Field(default=None, alias="nextCursor")
    has_next_page: bool = Field(alias="hasNextPage")


class ListedPod(ApiModel):
    """A pod or a queued pod as the list endpoints return it (id, name, status, createdAt).

    An id outside `ITEM_ID_PATTERN` or an unparseable `createdAt` fails the whole page: the id
    goes into request paths and logs, so it must be harmless. A missing `createdAt` (the spec
    requires it on both kinds) is tolerated: the deadline check then measures from the pass
    start, which is later than any creation and so never flags a plausible deadline.
    """

    pod_id: str = Field(alias="id", pattern=ITEM_ID_PATTERN)
    name: str
    status: str
    created_at: AwareDatetime | None = Field(default=None, alias="createdAt")


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
