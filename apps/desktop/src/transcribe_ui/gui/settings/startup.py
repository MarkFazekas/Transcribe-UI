"""The first start: when the set-up wizard opens, and the window's start-up notice (plan m6 §1 "Layout").

Qt-free. The wizard opens once per session, at start-up: at accounts while an R2 credential is
missing (`credentials_status`), at the cloud setup while a Runpod key is set and
`cloud_setup_status` finds an object missing or outdated. It can be closed; the notice then
stays until the cause is gone (accounts, cloud setup), and a failed cloud check shows a notice
with Retry instead of the wizard. Each notice can be hidden for the session.
"""

from collections.abc import Callable
from functools import partial

from transcribe_ui.gui.settings.accounts import AccountsViewModel
from transcribe_ui.gui.settings.cloud_setup import CloudSetupViewModel
from transcribe_ui.gui.settings.constants import NoticeKind, WizardStep
from transcribe_ui.gui.settings.status import r2_missing
from transcribe_ui.gui.viewmodel import ViewModel

# Called once a start-up step's request answered.
type Proceed = Callable[[], None]
# Opens the set-up wizard at a step.
type OpenWizard = Callable[[WizardStep], None]


class FirstStart(ViewModel):
    """The start-up steps of the accounts and the cloud setup, the wizard's trigger and the notice."""

    def __init__(self, accounts: AccountsViewModel, cloud: CloudSetupViewModel, open_wizard: OpenWizard) -> None:
        """Follow `accounts` and `cloud`; `open_wizard` shows the wizard."""
        super().__init__(accounts.executor)
        self._accounts = accounts
        self._cloud = cloud
        self._open_wizard = open_wizard
        self._wizard_opened = False
        self._hidden: NoticeKind | None = None
        accounts.subscribe(self.changed)
        cloud.subscribe(self.changed)

    @property
    def notice(self) -> NoticeKind | None:
        """Return the notice the window shows now (None: none, or the user hid it)."""
        shown = self._cause()
        return None if shown == self._hidden else shown

    def credentials_step(self, proceed: Proceed) -> None:
        """Start-up step: read the credentials' status; open the wizard at accounts while R2 is incomplete."""
        self._accounts.load(partial(self._credentials_known, proceed))

    def cloud_step(self, proceed: Proceed) -> None:
        """Start-up step: with a Runpod key, check the cloud setup; open the wizard there if it is needed."""
        if self._cloud.key_set:
            self._cloud.check(partial(self._cloud_known, proceed))
        else:
            proceed()

    def hide(self) -> None:
        """Hide the notice shown now for the rest of the session (another notice still shows)."""
        self._hidden = self._cause()
        self.changed()

    def _cause(self) -> NoticeKind | None:
        status = self._accounts.shown.status
        if status is not None and r2_missing(status):
            return "accounts"
        if not self._cloud.key_set or self._cloud.running:
            return None
        if self._cloud.error is not None:
            return "cloud_error"
        return "cloud_setup" if self._cloud.setup_needed else None

    def _credentials_known(self, proceed: Proceed) -> None:
        status = self._accounts.shown.status
        if status is not None and r2_missing(status):
            self._open("accounts")
        proceed()

    def _cloud_known(self, proceed: Proceed) -> None:
        if self._cloud.setup_needed:
            self._open("cloud_setup")
        proceed()

    def _open(self, step: WizardStep) -> None:
        if not self._wizard_opened:
            self._wizard_opened = True
            self._open_wizard(step)
