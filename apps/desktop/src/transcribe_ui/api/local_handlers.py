"""Handlers of the requests that never open the database (architecture §3.1).

App info, scans, settings, credentials and the cloud setup: a second process (the CLI while
the GUI holds the writer lock) can run them. `api/routes.py` marks them so in the dispatch table.
"""

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from transcribe_ui.api.classes import Response
from transcribe_ui.api.info import get_app_info
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.credentials.services import check_credentials, credentials_status, store_credentials
from transcribe_ui.discovery.services import preview_selection, scan_tree
from transcribe_ui.runpod.setup import cloud_setup_status, setup_cloud
from transcribe_ui.state.requests import get_settings, update_settings

# A request handler: the request and its context in, the response out.
type RequestRunner = Callable[[Any, RequestContext], Response]


def local_handlers() -> Mapping[str, RequestRunner]:
    """Return the handlers of the requests that need no database, by request `action`."""
    return MappingProxyType(
        {
            "get_app_info": get_app_info,
            "scan_tree": scan_tree,
            "preview_selection": preview_selection,
            "get_settings": get_settings,
            "update_settings": update_settings,
            "store_credentials": store_credentials,
            "credentials_status": credentials_status,
            "check_credentials": check_credentials,
            "setup_cloud": setup_cloud,
            "cloud_setup_status": cloud_setup_status,
        },
    )
