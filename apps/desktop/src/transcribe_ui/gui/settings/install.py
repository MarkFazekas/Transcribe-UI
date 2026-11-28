"""Wires the settings area into the main window: the page, the notice, the wizard, start-up steps, restart.

`gui/areas.py` calls `install_settings(window)` once. The start-up steps: `get_settings` and
`get_app_info` (the window title's version) in the settings slot, `credentials_status` in the
credentials slot (the wizard at accounts while R2 is incomplete), and with a Runpod key
`cloud_setup_status` in the reconcile slot (the wizard at the cloud setup when it is needed).
"""

from functools import partial
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QVBoxLayout

from transcribe_ui.gui.constants import PAGE_SETTINGS, SECTION_ACCOUNTS, SECTION_CLOUD_SETUP
from transcribe_ui.gui.settings.about import window_title
from transcribe_ui.gui.settings.constants import NoticeKind, WizardStep
from transcribe_ui.gui.settings.hooks import AreaHooks
from transcribe_ui.gui.settings.models import ModelPorts, SettingsModels, build_models
from transcribe_ui.gui.settings.notice_view import NoticeAction, NoticeBar, RestartQuestion
from transcribe_ui.gui.settings.view import SettingsPage
from transcribe_ui.gui.settings.wizard import SetupWizard
from transcribe_ui.gui.signals import StartupSlot
from transcribe_ui.gui.texts import page_title

if TYPE_CHECKING:
    from transcribe_ui.gui.shell import MainWindow


class SettingsArea:
    """The settings area of one window: its view-models, page, notice, wizard and restart question."""

    def __init__(self, window: "MainWindow") -> None:
        """Build the area over the window's context and register its page and start-up steps."""
        self._window = window
        context = window.context
        hooks = AreaHooks(window.show_error, context.signals.settings_changed.emit, self.ask_restart)
        ports = ModelPorts(context.executor, context.activity, context.log_dir, context.system_language)
        self.models = build_models(ports, hooks, self.open_wizard)
        self.page = SettingsPage(self.models, window.show_error, window)
        window.pages.add_page(PAGE_SETTINGS, page_title(PAGE_SETTINGS), self.page, needs_database=False)
        self.notice = self._notice()
        self.wizard: SetupWizard | None = None
        self.question: RestartQuestion | None = None
        context.signals.open_settings.connect(self.page.show_section)
        self.models.about.subscribe(self._show_version)
        window.closing.when_kept_open(context.restart.withdraw)
        _register_steps(self.models, window)

    def open_wizard(self, step: WizardStep) -> None:
        """Open the set-up wizard at `step` (window-modal; it can be closed)."""
        if self.wizard is None:
            # One wizard per window: its views follow the view-models for as long as the window lives.
            models = self.models
            window = self._window
            self.wizard = SetupWizard(models.accounts, models.cloud, window.show_error, window)
        self.wizard.start(step)

    def ask_restart(self) -> None:
        """Ask to restart now so the saved language applies (Q32); Restart now closes through Q30."""
        question = RestartQuestion(self._window)
        question.accepted.connect(self._restart)
        self.question = question
        question.open()

    def _restart(self) -> None:
        self._window.context.restart.request()
        self._window.close()

    def _show_version(self) -> None:
        self._window.setWindowTitle(window_title(self.models.about.version))

    def _notice(self) -> NoticeBar:
        signals = self._window.context.signals
        cloud = self.models.cloud
        actions: dict[NoticeKind, NoticeAction] = {
            "accounts": partial(signals.open_settings.emit, SECTION_ACCOUNTS),
            "cloud_setup": partial(signals.open_settings.emit, SECTION_CLOUD_SETUP),
            "cloud_error": cloud.check,
        }
        error_text = partial(_error_text, self.models)
        notice = NoticeBar(self.models.first_start, actions, error_text, self._window)
        central = self._window.centralWidget()
        layout = None if central is None else central.layout()
        if isinstance(layout, QVBoxLayout):
            layout.insertWidget(0, notice)
        return notice


def _error_text(models: SettingsModels) -> str:
    error = models.cloud.error
    return "" if error is None else error.message


def _register_steps(models: SettingsModels, window: "MainWindow") -> None:
    hooks = window.context.hooks
    hooks.register(StartupSlot.SETTINGS, models.preferences.load)
    hooks.register(StartupSlot.SETTINGS, models.about.load)
    hooks.register(StartupSlot.CREDENTIALS, models.first_start.credentials_step)
    hooks.register(StartupSlot.RECONCILE, models.first_start.cloud_step)


def install_settings(window: "MainWindow") -> SettingsArea:
    """Build the settings area of `window` and register it."""
    return SettingsArea(window)
