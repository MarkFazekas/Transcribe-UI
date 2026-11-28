"""The About section: the app's and the data format's versions, and the log folder (`get_app_info`; Qt-free)."""

from collections.abc import Callable
from functools import partial
from pathlib import Path

from transcribe_ui.api.classes import GetAppInfoRequest, GetAppInfoResponse, Response
from transcribe_ui.gui.constants import WINDOW_TITLE
from transcribe_ui.gui.executor import Executor
from transcribe_ui.gui.viewmodel import ViewModel

# Called once a start-up step's request answered.
type Proceed = Callable[[], None]


def window_title(version: str) -> str:
    """Return the window's title: the app's name and, once known, its version."""
    return f"{WINDOW_TITLE} {version}" if version else WINDOW_TITLE


class AboutViewModel(ViewModel):
    """The versions of the running app and where its log is."""

    def __init__(self, executor: Executor, log_dir: Path) -> None:
        """Send through `executor`; `log_dir` is the GUI log's folder."""
        super().__init__(executor)
        self.log_dir = log_dir
        # The app's version ("" until `get_app_info` answered) and the data contract's.
        self.version = ""
        self.schema_version = ""

    def load(self, proceed: Proceed | None = None) -> None:
        """Ask for the versions (`get_app_info`); `proceed` (of a start-up step) is called when answered."""
        request = GetAppInfoRequest(action="get_app_info")
        self.executor.submit(request, partial(self._loaded, proceed))

    def _loaded(self, proceed: Proceed | None, response: Response) -> None:
        if isinstance(response, GetAppInfoResponse):
            self.version = response.app_version
            self.schema_version = response.contracts_schema_version
            self.changed()
        if proceed is not None:
            proceed()
