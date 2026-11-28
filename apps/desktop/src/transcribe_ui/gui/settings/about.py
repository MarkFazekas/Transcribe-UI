"""The About section: the versions, the log folder and the third-party licences (Qt-free).

The versions come from `get_app_info`; the licence notices a release build bundles from
`get_third_party_notices`, asked for when their dialog opens and kept once loaded.
"""

from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from transcribe_ui.api.classes import (
    ErrorBody,
    ErrorResponse,
    GetAppInfoRequest,
    GetAppInfoResponse,
    GetThirdPartyNoticesRequest,
    GetThirdPartyNoticesResponse,
    Response,
)
from transcribe_ui.api.constants import ERROR_INTERNAL
from transcribe_ui.gui.constants import WINDOW_TITLE
from transcribe_ui.gui.executor import Executor
from transcribe_ui.gui.feedback import ErrorView, error_view
from transcribe_ui.gui.viewmodel import ViewModel
from transcribe_ui.i18n.catalog import tr

if TYPE_CHECKING:
    from transcribe_ui.gui.settings.constants import NoticesState

# Called once a start-up step's request answered.
type Proceed = Callable[[], None]


def window_title(version: str) -> str:
    """Return the window's title: the app's name and, once known, its version."""
    return f"{WINDOW_TITLE} {version}" if version else WINDOW_TITLE


class AboutViewModel(ViewModel):
    """The versions of the running app, where its log is, and the third-party licence notices."""

    def __init__(self, executor: Executor, log_dir: Path) -> None:
        """Send through `executor`; `log_dir` is the GUI log's folder."""
        super().__init__(executor)
        self.log_dir = log_dir
        # The app's version ("" until `get_app_info` answered) and the data contract's.
        self.version = ""
        self.schema_version = ""
        self.notices_state: NoticesState = "not_loaded"
        # The loaded notices; None when the app bundles none (it runs from source).
        self.notices: str | None = None
        # The error of the last load of the notices; None unless it failed.
        self.notices_error: ErrorView | None = None

    def load(self, proceed: Proceed | None = None) -> None:
        """Ask for the versions (`get_app_info`); `proceed` (of a start-up step) is called when answered."""
        request = GetAppInfoRequest(action="get_app_info")
        self.executor.submit(request, partial(self._loaded, proceed))

    def load_notices(self) -> None:
        """Ask for the third-party licence notices (`get_third_party_notices`) unless loading or loaded.

        A failed load is asked for again (opening the dialog again retries it).
        """
        if self.notices_state in {"loading", "loaded"}:
            return
        self.notices_state = "loading"
        self.notices_error = None
        self.changed()
        request = GetThirdPartyNoticesRequest(action="get_third_party_notices")
        self.executor.submit(request, self._notices_loaded)

    def notices_message(self) -> str:
        """Return the line the notices dialog shows instead of the notices; "" once they are shown."""
        if self.notices_state == "loaded":
            if self.notices is None:
                return tr("The third-party licences are included only in release builds.")
            return ""
        if self.notices_error is not None:
            return self.notices_error.message
        return tr("Loading the third-party licences…")

    def _loaded(self, proceed: Proceed | None, response: Response) -> None:
        if isinstance(response, GetAppInfoResponse):
            self.version = response.app_version
            self.schema_version = response.contracts_schema_version
            self.changed()
        if proceed is not None:
            proceed()

    def _notices_loaded(self, response: Response) -> None:
        if isinstance(response, GetThirdPartyNoticesResponse):
            self.notices_state = "loaded"
            self.notices = response.notices
        elif isinstance(response, ErrorResponse):
            self.notices_state = "failed"
            self.notices_error = error_view(response)
        else:
            # Never the answer to this request: a backend bug, shown as an internal error; opening
            # the dialog again asks again.
            self.notices_state = "failed"
            body = ErrorBody(code=ERROR_INTERNAL, message="Unexpected answer to the notices request.")
            self.notices_error = error_view(ErrorResponse(action="get_third_party_notices", error=body))
        self.changed()
