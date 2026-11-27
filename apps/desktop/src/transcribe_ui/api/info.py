"""The `get_app_info` request."""

from importlib.metadata import version

from transcribe_contracts.constants import SCHEMA_VERSION
from transcribe_ui.api.classes import GetAppInfoRequest, GetAppInfoResponse
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.constants import DISTRIBUTION_NAME


def app_version() -> str:
    """Return the version of the installed app."""
    return version(DISTRIBUTION_NAME)


def get_app_info(_request: GetAppInfoRequest, _context: RequestContext) -> GetAppInfoResponse:
    """Return the app and data contract versions; the same on every machine."""
    return GetAppInfoResponse(app_version=app_version(), contracts_schema_version=SCHEMA_VERSION)
