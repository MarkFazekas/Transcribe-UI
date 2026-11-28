"""An installation without a Runpod key in the window (pytest-qt, offscreen; plan m6 M6.13).

Two installations share one bucket: `runs_world` (with the Runpod key) uploads the job and
transcribes it; the window runs over `r2_only_world` through `QtExecutor`. Without the key the
Cloud page shows start, stop and the machines list unavailable with the reason, and the
start-up still checks the runs and results. "Jobs of other computers" lists the job; Download
adopts it through the confirmation dialog, opens its download dialog with `mirror` pre-selected
and checks the cloud, and the subtitles are mirrored into a chosen folder. The poll goes on only
while the check says that results are awaited.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from pytestqt.qtbot import QtBot

from support.batches import FILM, FIRST_BATCH_ID
from support.cloud import RecordingRunner
from support.gated import wait_for
from support.gui import window_over
from support.others_gui import uploaded_elsewhere
from support.runs import RunsWorld
from transcribe_ui.gui.cloud.constants import REFRESH_INTERVAL_MS
from transcribe_ui.gui.cloud.sections import KeyBanner
from transcribe_ui.gui.cloud.view import CloudPage
from transcribe_ui.gui.constants import PAGE_CLOUD, PAGE_JOBS, PAGE_SETTINGS
from transcribe_ui.gui.dialogs import ConfirmationDialog
from transcribe_ui.gui.download.constants import STRATEGY_CHOICES
from transcribe_ui.gui.download.install import DownloadArea
from transcribe_ui.gui.download.others_view import OtherJobsSection
from transcribe_ui.gui.download.view import DownloadDialog
from transcribe_ui.gui.jobs.view import JobsPage
from transcribe_ui.gui.shell import MainWindow

# The hint of an action that needs the Runpod key.
NEEDS_KEY = "Needs a Runpod API key on this computer."


@pytest.fixture
def runner(r2_only_world: RunsWorld) -> RecordingRunner:
    """Return the R2-only installation's backend, recording the requests."""
    return RecordingRunner(r2_only_world.leg.backend)


@pytest.fixture
def window(qtbot: QtBot, runner: RecordingRunner, tmp_path: Path) -> Iterator[MainWindow]:
    """Return the window over the R2-only installation, started up; after the test, wait until no call runs."""
    created = window_over(qtbot, runner, tmp_path)
    done: list[bool] = []
    created.context.hooks.run(lambda: done.append(True))
    wait_for(qtbot, lambda: bool(done) and not created.busy())
    yield created
    wait_for(qtbot, lambda: not created.busy())


def _page(window: MainWindow) -> CloudPage:
    page = window.pages.page(PAGE_CLOUD)
    assert isinstance(page, CloudPage)
    return page


def _poll_timer(window: MainWindow) -> QTimer:
    found = [timer for timer in window.findChildren(QTimer) if timer.interval() == REFRESH_INTERVAL_MS]
    assert len(found) == 1, found
    return found[0]


def _adopt(qtbot: QtBot, window: MainWindow) -> DownloadDialog:
    """List the cloud's jobs, Download the uploaded one, confirm; return its download dialog once checked."""
    section = _page(window).findChild(OtherJobsSection)
    assert isinstance(section, OtherJobsSection)
    section.look_button.click()
    wait_for(qtbot, lambda: FIRST_BATCH_ID in section.download_buttons)
    state = section.table.item(0, 3)
    assert state is not None
    assert state.text() in {"Transcribed", "Waiting for transcription"}
    section.download_buttons[FIRST_BATCH_ID].click()
    wait_for(qtbot, lambda: isinstance(window.dialog, ConfirmationDialog) and window.dialog.isVisible())
    question = window.dialog
    assert isinstance(question, ConfirmationDialog)
    question.confirm_button.click()
    area = window.findChild(DownloadArea)
    assert isinstance(area, DownloadArea)
    wait_for(qtbot, lambda: FIRST_BATCH_ID in area.dialogs and area.dialogs[FIRST_BATCH_ID].isVisible())
    wait_for(qtbot, lambda: not window.busy() and not section.download_buttons)
    return area.dialogs[FIRST_BATCH_ID]


def test_without_a_key_the_cloud_page_says_why(window: MainWindow, runner: RecordingRunner) -> None:
    """Start and the machines check are disabled with the reason; the results are still checked at start-up."""
    actions = runner.actions()
    assert ("refresh_status" in actions, "reconcile_cloud" in actions) == (True, False)
    page = _page(window)
    banner = page.findChild(KeyBanner)
    assert isinstance(banner, KeyBanner)
    assert not banner.isHidden()
    start = page.run_panel.buttons.start_button
    machines = page.machines
    # Nothing of this computer awaits results, so the poll does not run.
    polling = _poll_timer(window).isActive()
    shown = (start.isEnabled(), start.toolTip(), machines.check_button.isEnabled(), machines.state.text(), polling)
    assert shown == (False, NEEDS_KEY, False, NEEDS_KEY, False)
    banner.accounts_button.click()
    assert window.pages.current_page() == PAGE_SETTINGS


def test_another_computers_job_is_adopted_and_mirrored(
    qtbot: QtBot,
    runs_world: RunsWorld,
    window: MainWindow,
    tmp_path: Path,
) -> None:
    """Download adopts the job, opens its dialog on `mirror`, checks it; the subtitles are mirrored into the folder."""
    uploaded_elsewhere(runs_world, transcribed=True)
    dialog = _adopt(qtbot, window)
    assert dialog.options.strategy.currentIndex() == STRATEGY_CHOICES.index("mirror")
    assert not _poll_timer(window).isActive(), "the check read every result: nothing is awaited"
    jobs = window.pages.page(PAGE_JOBS)
    assert isinstance(jobs, JobsPage)
    listed = {row.batch_id: row.labels for row in jobs.list_view.model.rows}
    assert "Uploaded from another computer" in listed[FIRST_BATCH_ID]
    out = tmp_path / "mirror"
    dialog.options.output_folder.setText(str(out))
    dialog.options.output_folder.editingFinished.emit()
    wait_for(qtbot, dialog.download_button.isEnabled)
    dialog.download_button.click()
    film = out / "Movies" / FILM.replace(".mkv", ".en.srt")
    wait_for(qtbot, lambda: film.is_file() and not window.busy())


def test_the_poll_follows_an_awaiting_adopted_job(
    qtbot: QtBot,
    runs_world: RunsWorld,
    window: MainWindow,
) -> None:
    """A job not transcribed yet: after its adoption the check says results are awaited, and the poll goes on."""
    uploaded_elsewhere(runs_world, transcribed=False)
    assert not _poll_timer(window).isActive()
    _adopt(qtbot, window)
    assert _poll_timer(window).isActive()
