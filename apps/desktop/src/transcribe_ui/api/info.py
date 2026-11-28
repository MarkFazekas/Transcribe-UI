"""The `get_app_info` request."""

from importlib.metadata import version
from importlib.resources import files
from importlib.resources.abc import Traversable

from transcribe_contracts.constants import SCHEMA_VERSION
from transcribe_ui.api.classes import GetAppInfoRequest, GetAppInfoResponse
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.constants import DISTRIBUTION_NAME, PACKAGE_NAME, VERSION_STAMP_FILE


def stamped_version(stamp: Traversable) -> str | None:
    """Return the version a release build stamped into `stamp`, or None when there is no stamp."""
    if not stamp.is_file():
        return None
    return stamp.read_text(encoding="utf-8").strip() or None


def app_version() -> str:
    """Return the version of the app: a release build's stamp, else the installed distribution's version."""
    stamped = stamped_version(files(PACKAGE_NAME).joinpath(VERSION_STAMP_FILE))
    return version(DISTRIBUTION_NAME) if stamped is None else stamped


def get_app_info(_request: GetAppInfoRequest, _context: RequestContext) -> GetAppInfoResponse:
    """Return the app and data contract versions; the same on every machine."""
    return GetAppInfoResponse(app_version=app_version(), contracts_schema_version=SCHEMA_VERSION)
