"""Descriptions of Runpod API calls: method, path, the key scope and how failures are treated."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from urllib.parse import quote

import httpx


def item_path(path: str, item_id: str) -> str:
    """Return the path of one object; the id (from an API response) stays one path segment."""
    return f"{path}/{quote(item_id, safe='')}"


@dataclass(frozen=True)
class Call:
    """One API operation."""

    method: str
    # Path below the base URL.
    path: str
    # What is acted on, for messages ("pods", "template").
    what: str
    # The API key scope the operation needs (named in a 403 error).
    scope: str
    # True for a POST that creates something (never re-sent after an ambiguous outcome).
    creates: bool = False
    # True if a 404 means "does not exist" (the call then returns None).
    missing_ok: bool = False
    # Query string parameters.
    query: Mapping[str, str] | None = None
    # JSON body; may hold a secret value, so it is kept out of the repr.
    body: Mapping[str, object] | None = field(default=None, repr=False)

    def request(self, client: httpx.Client) -> httpx.Request:
        """Return the call as an HTTP request on the client's base URL."""
        method = self.method
        body = self.body
        return client.build_request(method, self.path, params=self.query, json=body)

    @property
    def label(self) -> str:
        """Return the call as named in messages, such as `GET pods`."""
        return f"{self.method} {self.what}"


@dataclass(frozen=True)
class Endpoint:
    """A collection of the API (`/pods`, `/templates`, ...) and the calls on it."""

    path: str
    # Singular and plural noun, for messages.
    one: str
    many: str
    read_scope: str
    write_scope: str

    def listing(self, query: Mapping[str, str] | None = None) -> Call:
        """Return the call listing the collection."""
        return Call("GET", self.path, self.many, self.read_scope, query=query)

    def read(self, item_id: str) -> Call:
        """Return the call reading one object (None if unknown)."""
        path = item_path(self.path, item_id)
        return Call("GET", path, self.one, self.read_scope, missing_ok=True)

    def create(self, body: Mapping[str, object]) -> Call:
        """Return the call creating an object (never re-sent after an ambiguous outcome)."""
        call = Call("POST", self.path, self.one, self.write_scope, body=body)
        return replace(call, creates=True)

    def update(self, item_id: str, body: Mapping[str, object]) -> Call:
        """Return the call updating an object (PATCH: idempotent)."""
        path = item_path(self.path, item_id)
        return Call("PATCH", path, self.one, self.write_scope, body=body)

    def delete(self, item_id: str) -> Call:
        """Return the call deleting an object (a 404 means it is gone already)."""
        path = item_path(self.path, item_id)
        return Call("DELETE", path, self.one, self.write_scope, missing_ok=True)
