"""The cloud setup page: which Runpod objects are missing or outdated, and Set up / Update (§3.7).

Qt-free. `cloud_setup_status` lists the objects; `setup_cloud` runs in the activity panel (a long
request: Cancel, and Resume re-sends it; it is idempotent). Both need the Runpod API key: without
one the page says that another computer starts the transcription (R2-only role, §3.9).
"""

from collections.abc import Callable
from dataclasses import dataclass

from transcribe_ui.api.classes import ErrorResponse, Response
from transcribe_ui.api.constants import ERROR_CANCELLED
from transcribe_ui.gui.activity import Activity
from transcribe_ui.gui.executor import Executor
from transcribe_ui.gui.feedback import ErrorView, error_view
from transcribe_ui.gui.settings import account_texts
from transcribe_ui.gui.settings.accounts import AccountsViewModel
from transcribe_ui.gui.settings.constants import SETUP_CLOUD_KEY, SETUP_NEEDED_STATES
from transcribe_ui.gui.settings.status import runpod_key_set
from transcribe_ui.gui.viewmodel import ViewModel
from transcribe_ui.i18n.catalog import tr
from transcribe_ui.runpod.classes import (
    CloudSetupStatusRequest,
    CloudSetupStatusResponse,
    SetupCloudRequest,
    SetupCloudResponse,
)

# Called once a start-up step's request answered.
type Proceed = Callable[[], None]


@dataclass(frozen=True)
class CloudObjectRow:
    """One object of the cloud setup as the page lists it."""

    kind: str
    name: str
    state: str


class CloudSetupViewModel(ViewModel):
    """The cloud setup's status, Check, and Set up / Update."""

    def __init__(self, executor: Executor, activity: Activity, accounts: AccountsViewModel) -> None:
        """Send checks through `executor` and the setup through `activity`; the key comes from `accounts`."""
        super().__init__(executor)
        self._activity = activity
        self._accounts = accounts
        self.status: CloudSetupStatusResponse | None = None
        # The error of the last check or setup (shown in the page); None after a success.
        self.error: ErrorView | None = None
        self.message = ""
        self.checking = False
        # `proceed` of the callers waiting for the check in flight.
        self._waiting: list[Proceed] = []
        accounts.subscribe(self.changed)
        activity.subscribe(self.changed)

    @property
    def key_set(self) -> bool:
        """Return whether the Runpod API key is set (the page is usable only then)."""
        return runpod_key_set(self._accounts.shown.status)

    @property
    def running(self) -> bool:
        """Return whether `setup_cloud` is queued or running."""
        return self._activity.busy(SETUP_CLOUD_KEY)

    @property
    def setup_needed(self) -> bool:
        """Return whether the last check found an object missing or outdated (the first-start wizard opens)."""
        if self.status is None:
            return False
        return any(cloud_object.state in SETUP_NEEDED_STATES for cloud_object in self.status.cloud_objects)

    def rows(self) -> tuple[CloudObjectRow, ...]:
        """Return the objects of the last check."""
        if self.status is None:
            return ()
        return tuple(
            CloudObjectRow(account_texts.object_kind(found.kind), found.name, account_texts.object_state(found))
            for found in self.status.cloud_objects
        )

    def check(self, proceed: Proceed | None = None) -> None:
        """Compare the Runpod objects with what the app needs (`cloud_setup_status`); `proceed` when answered.

        While a check runs, no second one is sent: `proceed` waits for the running one.
        """
        if proceed is not None:
            self._waiting.append(proceed)
        if self.checking:
            return
        self.checking = True
        self.message = ""
        self.changed()
        request = CloudSetupStatusRequest(action="cloud_setup_status")
        self.executor.submit(request, self._checked)

    def set_up(self) -> None:
        """Create or update the objects (`setup_cloud`, in the activity panel); then check again."""
        if self.running:
            return
        self.error = None
        self.message = ""
        self.changed()
        request = SetupCloudRequest(action="setup_cloud")
        self._activity.start(request, tr("Cloud setup"), self._set_up_done, SETUP_CLOUD_KEY)

    def _checked(self, response: Response) -> None:
        self.checking = False
        if isinstance(response, CloudSetupStatusResponse):
            self.status = response
            self.error = None
        elif isinstance(response, ErrorResponse):
            self.error = error_view(response)
        self.changed()
        waiting = self._waiting
        self._waiting = []
        for proceed in waiting:
            proceed()

    def _set_up_done(self, response: Response) -> None:
        if isinstance(response, SetupCloudResponse):
            self.check()
            # After the check's own reset: the summary stays until the next check.
            self.message = account_texts.setup_summary(response)
            self.changed()
            return
        if isinstance(response, ErrorResponse) and response.error.code != ERROR_CANCELLED:
            self.error = error_view(response)
        self.changed()
