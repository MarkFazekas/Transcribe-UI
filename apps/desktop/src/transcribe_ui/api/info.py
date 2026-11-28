"""The `get_app_info` and `get_third_party_notices` requests."""

from importlib.metadata import version
from importlib.resources import files
from importlib.resources.abc import Traversable

from transcribe_contracts.constants import SCHEMA_VERSION
from transcribe_ui.api.classes import (
    GetAppInfoRequest,
    GetAppInfoResponse,
    GetThirdPartyNoticesRequest,
    GetThirdPartyNoticesResponse,
)
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.constants import DISTRIBUTION_NAME, PACKAGE_NAME, THIRD_PARTY_NOTICES_FILE, VERSION_STAMP_FILE


def stamped_version(stamp: Traversable) -> str | None:
    """Return the version a release build stamped into `stamp`, or None when there is no stamp."""
    if not stamp.is_file():
        return None
    return stamp.read_text(encoding="utf-8").strip() or None


def app_version() -> str:
    """Return the version of the app: a release build's stamp, else the installed distribution's version."""
    stamped = stamped_version(files(PACKAGE_NAME).joinpath(VERSION_STAMP_FILE))
    return version(DISTRIBUTION_NAME) if stamped is None else stamped


def bundled_notices() -> str | None:
    """Return the third-party licence notices a release build bundles, or None when run from source."""
    notices = files(PACKAGE_NAME).joinpath(THIRD_PARTY_NOTICES_FILE)
    if not notices.is_file():
        return None
    return notices.read_text(encoding="utf-8")


def get_app_info(_request: GetAppInfoRequest, _context: RequestContext) -> GetAppInfoResponse:
    """Return the app and data contract versions; the same on every machine."""
    return GetAppInfoResponse(app_version=app_version(), contracts_schema_version=SCHEMA_VERSION)


def get_third_party_notices(
    _request: GetThirdPartyNoticesRequest,
    _context: RequestContext,
) -> GetThirdPartyNoticesResponse:
    """Return the licence notices of the bundled third-party software (None from source)."""
    return GetThirdPartyNoticesResponse(notices=bundled_notices())
