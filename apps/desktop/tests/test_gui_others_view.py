"""An installation without a Runpod key in the window (pytest-qt, offscreen; plan m6 M6.13).

Two installations share one bucket: `runs_world` (with the Runpod key) uploads the job and
transcribes it; the window runs over `r2_only_world` through `QtExecutor`. Without the key the
Cloud page shows start, stop and the machines list unavailable with the reason, and the
start-up still checks the runs and results; a key stored in Settings meanwhile counts once
the page shows again. "Jobs of other computers" lists the job; Download adopts it through the
confirmation dialog, opens its download dialog with `mirror` pre-selected and checks the cloud,
and the subtitles are mirrored into a chosen folder. The poll goes on only while the check says
that results are awaited.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget
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
from transcribe_ui.gui.download.others_display import CloudJobRow
from transcribe_ui.gui.download.others_view import OtherJobsSection, OtherJobsTable
from transcribe_ui.gui.download.view import DownloadDialog
from transcribe_ui.gui.jobs.view import JobsPage
from transcribe_ui.gui.shell import MainWindow

# A Runpod key stored in Settings while the window is open.
KEY = "rp-stored-meanwhile"
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


def _listed_and_selected(qtbot: QtBot, window: MainWindow) -> OtherJobsSection:
    """Show the Cloud page, list the cloud's jobs and select the uploaded one (Download is disabled until then)."""
    window.pages.show_page(PAGE_CLOUD)
    section = _page(window).findChild(OtherJobsSection)
    assert isinstance(section, OtherJobsSection)
    section.look_button.click()
    wait_for(qtbot, lambda: section.table.rows.row_of(FIRST_BATCH_ID) is not None)
    assert section.table.rows.index(0, 3).data() in {"Transcribed", "Waiting for transcription"}
    download = section.download_button
    assert (download.isEnabled(), download.toolTip()) == (False, "Select a job in the list first.")
    section.table.selectRow(0)
    assert download.isEnabled()
    return section


def _double_click(section: OtherJobsSection) -> None:
    """Double-click the first row (QTest sends only a double-click's second press, so a click comes first)."""
    table = section.table
    cell = table.visualRect(table.rows.index(0, 0)).center()
    left = Qt.MouseButton.LeftButton
    QTest.mouseClick(table.viewport(), left, Qt.KeyboardModifier.NoModifier, cell)
    QTest.mouseDClick(table.viewport(), left, Qt.KeyboardModifier.NoModifier, cell)


def _adopt(qtbot: QtBot, window: MainWindow, *, by_double_click: bool = False) -> DownloadDialog:
    """List the cloud's jobs, Download the uploaded one (or double-click it), confirm; return its dialog once checked."""
    section = _listed_and_selected(qtbot, window)
    if by_double_click:
        _double_click(section)
    else:
        section.download_button.click()
    wait_for(qtbot, lambda: isinstance(window.dialog, ConfirmationDialog) and window.dialog.isVisible())
    question = window.dialog
    assert isinstance(question, ConfirmationDialog)
    question.confirm_button.click()
    area = window.findChild(DownloadArea)
    assert isinstance(area, DownloadArea)
    wait_for(qtbot, lambda: FIRST_BATCH_ID in area.dialogs and area.dialogs[FIRST_BATCH_ID].isVisible())
    wait_for(qtbot, lambda: not window.busy() and not section.table.rows.rows)
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


def test_a_key_stored_meanwhile_counts_on_show(
    qtbot: QtBot,
    r2_only_world: RunsWorld,
    window: MainWindow,
) -> None:
    """The Cloud page asks again each time it shows: a key stored in Settings meanwhile enables Start."""
    page = _page(window)
    window.pages.show_page(PAGE_CLOUD)
    start = page.run_panel.buttons.start_button
    wait_for(qtbot, lambda: not window.busy())
    assert not start.isEnabled()
    stored = r2_only_world.leg.backend.execute_json(json.dumps({"action": "store_credentials", "runpod_api_key": KEY}))
    assert stored.status == "ok", stored
    window.pages.show_page(PAGE_JOBS)
    window.pages.show_page(PAGE_CLOUD)
    wait_for(qtbot, lambda: start.isEnabled() and not window.busy())
    banner = page.findChild(KeyBanner)
    assert isinstance(banner, KeyBanner)
    assert (banner.isHidden(), start.toolTip(), page.machines.check_button.isEnabled()) == (True, "", True)


def test_a_double_click_downloads_the_job(qtbot: QtBot, runs_world: RunsWorld, window: MainWindow) -> None:
    """A double-click on a listed job does what Download does: the confirmation, then its download dialog."""
    uploaded_elsewhere(runs_world, transcribed=False)
    dialog = _adopt(qtbot, window, by_double_click=True)
    assert dialog.options.strategy.currentIndex() == STRATEGY_CHOICES.index("mirror")


def test_a_new_list_keeps_the_selected_job(qtbot: QtBot) -> None:
    """A list whose rows changed keeps the selected job selected while it is still listed."""
    holder = QWidget()
    qtbot.addWidget(holder)
    table = OtherJobsTable(holder)
    first, second, third = (CloudJobRow(f"batch_2026010{day}_000000", "1", "1 min", "Transcribed") for day in "123")
    table.show_rows((first, second))
    table.selectRow(1)
    table.show_rows((third, second))
    kept = table.selected()
    table.show_rows((third,))
    assert (kept, table.selected()) == (second.batch_id, None)


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
