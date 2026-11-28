"""The areas of the window: each part's install function, called once by the shell (one line each).

An install function registers its area's page (`PageArea.add_page` replaces the placeholder),
connects the bus signals the area handles (`MainWindow.connect_handler` for action signals) and
adds its start-up steps. The shell then answers the action signals nobody handles with "Not
available yet".
"""

from typing import TYPE_CHECKING

from transcribe_ui.gui.cloud.install import install_cloud
from transcribe_ui.gui.download.install import install_download
from transcribe_ui.gui.jobs.install import install_jobs
from transcribe_ui.gui.selection.install import install_selection
from transcribe_ui.gui.settings.install import install_settings

if TYPE_CHECKING:
    from transcribe_ui.gui.shell import MainWindow


def install_areas(window: "MainWindow") -> None:
    """Install every built area in the window."""
    install_jobs(window)
    install_settings(window)
    install_selection(window)
    install_cloud(window)
    install_download(window)
