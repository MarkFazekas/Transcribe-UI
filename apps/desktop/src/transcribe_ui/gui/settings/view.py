"""The settings page: preferences, accounts, cloud setup and about, one tab each (architecture §3.1 "GUI")."""

from PySide6.QtWidgets import QTabWidget, QWidget

from transcribe_ui.gui.settings.about_view import AboutView
from transcribe_ui.gui.settings.accounts_view import AccountsView
from transcribe_ui.gui.settings.cloud_view import CloudSetupView, ShowError
from transcribe_ui.gui.settings.constants import SECTIONS
from transcribe_ui.gui.settings.models import SettingsModels
from transcribe_ui.gui.settings.preferences_view import PreferencesView
from transcribe_ui.gui.settings.texts import section_title


class SettingsPage(QTabWidget):
    """The settings page; `open_settings(section)` selects a tab."""

    def __init__(self, models: SettingsModels, show_error: ShowError, parent: QWidget | None = None) -> None:
        """Build a tab per section over the area's view-models."""
        super().__init__(parent)
        self.models = models
        self.preferences = PreferencesView(models.preferences, self)
        self.accounts = AccountsView(models.accounts, self)
        self.cloud_setup = CloudSetupView(models.cloud, show_error, self)
        self.about = AboutView(models.about, self)
        for section, widget in zip(
            SECTIONS, (self.preferences, self.accounts, self.cloud_setup, self.about), strict=True
        ):
            self.addTab(widget, section_title(section))

    def show_section(self, section: str) -> None:
        """Select a section's tab (an unknown or empty section: the first)."""
        self.setCurrentIndex(SECTIONS.index(section) if section in SECTIONS else 0)
