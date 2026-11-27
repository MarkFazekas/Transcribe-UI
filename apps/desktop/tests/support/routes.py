"""Dispatch tables of the local-state tests."""

from types import MappingProxyType

from transcribe_ui.api.info import get_app_info
from transcribe_ui.api.routes import Route, Routes, default_routes


def database_routes() -> Routes:
    """Return the production table with `get_app_info` marked as a database request.

    M5.2 has no production request that needs the database yet; this one stands in for them.
    """
    routes = dict(default_routes())
    routes["get_app_info"] = Route(run=get_app_info, needs_database=True)
    return MappingProxyType(routes)
