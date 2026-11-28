"""The settings area's view-models, built together (Qt-free)."""

from dataclasses import dataclass
from pathlib import Path

from transcribe_ui.gui.activity import Activity
from transcribe_ui.gui.executor import Executor
from transcribe_ui.gui.settings.about import AboutViewModel
from transcribe_ui.gui.settings.accounts import AccountsViewModel
from transcribe_ui.gui.settings.cloud_setup import CloudSetupViewModel
from transcribe_ui.gui.settings.hooks import AreaHooks
from transcribe_ui.gui.settings.startup import FirstStart, OpenWizard
from transcribe_ui.gui.settings.viewmodel import PreferencesViewModel
from transcribe_ui.i18n.constants import Language


@dataclass(frozen=True)
class ModelPorts:
    """What the area's view-models are built over."""

    executor: Executor
    # Runs `setup_cloud` (a long request with Cancel and Resume).
    activity: Activity
    # Folder of the GUI log (About).
    log_dir: Path
    # The language `system` shows (the OS language read once at start-up).
    system_language: Language


@dataclass(frozen=True)
class SettingsModels:
    """The view-models of the settings page, the wizard and the start-up notice."""

    preferences: PreferencesViewModel
    accounts: AccountsViewModel
    cloud: CloudSetupViewModel
    about: AboutViewModel
    first_start: FirstStart


def build_models(ports: ModelPorts, hooks: AreaHooks, open_wizard: OpenWizard) -> SettingsModels:
    """Return the area's view-models over `ports`."""
    executor = ports.executor
    accounts = AccountsViewModel(executor, hooks)
    cloud = CloudSetupViewModel(executor, ports.activity, accounts)
    return SettingsModels(
        preferences=PreferencesViewModel(executor, hooks, ports.system_language),
        accounts=accounts,
        cloud=cloud,
        about=AboutViewModel(executor, ports.log_dir),
        first_start=FirstStart(accounts, cloud, open_wizard),
    )
