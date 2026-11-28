"""The first-start set-up wizard: accounts, then the cloud setup when a Runpod key is set (plan m6 §1 "Layout").

It shows the settings page's own sections (new widgets on the same view-models), so whatever is
done here also shows in Settings. It can be closed at any step ("Set up later").
"""

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from transcribe_ui.gui.settings.accounts import AccountsViewModel
from transcribe_ui.gui.settings.accounts_view import AccountsView
from transcribe_ui.gui.settings.cloud_setup import CloudSetupViewModel
from transcribe_ui.gui.settings.cloud_view import CloudSetupView, ShowError
from transcribe_ui.gui.settings.constants import OPTIONAL_CREDENTIAL, WizardStep
from transcribe_ui.i18n.catalog import tr


def _pages(
    accounts: AccountsViewModel, cloud: CloudSetupViewModel, show_error: ShowError, parent: QWidget
) -> QStackedWidget:
    pages = QStackedWidget(parent)
    pages.addWidget(AccountsView(accounts, pages))
    pages.addWidget(CloudSetupView(cloud, show_error, pages))
    return pages


class SetupWizard(QDialog):
    """Step 1 accounts, step 2 the cloud setup (only with a Runpod key); Back, Next / Finish, Set up later."""

    def __init__(
        self,
        accounts: AccountsViewModel,
        cloud: CloudSetupViewModel,
        show_error: ShowError,
        parent: QWidget | None = None,
    ) -> None:
        """Show the sections of `accounts` and `cloud`."""
        super().__init__(parent)
        self._accounts = accounts
        self._cloud = cloud
        self._closed = False
        self.setWindowTitle(tr("Set up Transcribe-UI"))
        self.step_label = QLabel(self)
        self.pages = _pages(accounts, cloud, show_error, self)
        self.back_button = QPushButton(tr("Back"), self)
        self.next_button = QPushButton(self)
        self._lay_out()
        # Values typed here and not stored would make Save look enabled over empty fields once the
        # wizard is closed (and `start` forgets what was typed elsewhere).
        self.finished.connect(self._close)
        self.finished.connect(accounts.discard_typed)
        accounts.subscribe(self._repaint)
        self._repaint()

    def start(self, step: WizardStep) -> None:
        """Open the wizard (window-modal) at `step`; what was typed elsewhere and not stored is forgotten."""
        self._closed = False
        self._accounts.discard_typed()
        self.show_step(step)
        self.open()

    def show_step(self, step: WizardStep) -> None:
        """Show a step; the cloud setup is checked when it has not been yet."""
        on_cloud = step == "cloud_setup"
        self.pages.setCurrentIndex(1 if on_cloud else 0)
        cloud = self._cloud
        unchecked = cloud.status is None and not cloud.checking
        if on_cloud and unchecked and cloud.key_set:
            cloud.check()
        self._repaint()

    def _lay_out(self) -> None:
        later_button = QPushButton(tr("Set up later"), self)
        later_button.clicked.connect(self.reject)
        self.back_button.clicked.connect(self._back)
        self.next_button.clicked.connect(self._next)
        row = QHBoxLayout()
        row.addWidget(later_button)
        row.addStretch()
        for button in (self.back_button, self.next_button):
            row.addWidget(button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.step_label)
        layout.addWidget(self.pages, 1)
        layout.addLayout(row)

    def _back(self) -> None:
        self.show_step("accounts")

    def _next(self) -> None:
        """On accounts, store what was typed first: move on once stored, stay with the hints when refused."""
        if self._accounts.shown.saving:
            # Moves on once, when the store in flight answered.
            return
        if self.pages.currentIndex() == 0 and self._accounts.shown.can_save:
            self._accounts.save(self._advance)
        else:
            self._advance()

    def _advance(self) -> None:
        if self._closed:
            # A store answered after "Set up later": the wizard stays closed.
            return
        if self.pages.currentIndex() == 0 and self._cloud.key_set:
            self.show_step("cloud_setup")
        else:
            self.accept()

    def _repaint(self) -> None:
        on_cloud = self.pages.currentIndex() == 1
        if on_cloud:
            self.step_label.setText(tr("Step 2 of 2: cloud setup"))
        else:
            self.step_label.setText(tr("Step 1 of 2: accounts"))
        self.back_button.setVisible(on_cloud)
        typed_key = OPTIONAL_CREDENTIAL in self._accounts.shown.typed
        last = on_cloud or not (self._cloud.key_set or typed_key)
        self.next_button.setText(tr("Finish") if last else tr("Next"))
        saving = self._accounts.shown.saving
        self.next_button.setEnabled(not saving)
        self.back_button.setEnabled(not saving)

    def _close(self) -> None:
        self._closed = True
