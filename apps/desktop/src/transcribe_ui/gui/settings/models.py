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


@dataclass(frozen=True)
class SettingsModels:
    """The view-models of the settings page, the wizard and the start-up notice."""

    preferences: PreferencesViewModel
    accounts: AccountsViewModel
    cloud: CloudSetupViewModel
    about: AboutViewModel
    first_start: FirstStart


def build_models(
    executor: Executor,
    activity: Activity,
    log_dir: Path,
    hooks: AreaHooks,
    open_wizard: OpenWizard,
) -> SettingsModels:
    """Return the area's view-models over `executor` (`setup_cloud` runs in `activity`)."""
    accounts = AccountsViewModel(executor, hooks)
    cloud = CloudSetupViewModel(executor, activity, accounts)
    return SettingsModels(
        preferences=PreferencesViewModel(executor, hooks),
        accounts=accounts,
        cloud=cloud,
        about=AboutViewModel(executor, log_dir),
        first_start=FirstStart(accounts, cloud, open_wizard),
    )
