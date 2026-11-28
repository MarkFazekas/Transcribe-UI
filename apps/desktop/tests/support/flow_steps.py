"""The user's steps of the GUI's end-to-end flow, in the CLI flow's order (plan m6 M6.11).

Settings (English before Hungarian, Test the accounts, Check the cloud setup), New job (scan, tick,
languages, the silent video in Hungarian only, Create job, confirmed), Continue to review, Upload,
Start transcription, the poll's interval three times (the simulated worker changes the cloud before
the second and the third, as in the CLI flow), Download (mirror), Retry, the retry's plan, and the
three cleanups. Each step waits until every call it caused has answered.
"""

from dataclasses import dataclass
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog
from pytestqt.qtbot import QtBot

from support.batches import FIRST_BATCH_ID, SILENT, media_dir
from support.cloud import ManualTimers
from support.end_to_end import EN, HU, WORKER_CHANGES, change_legs
from support.flow_window import (
    WAIT_MS,
    cloud_page,
    entries,
    finished_after,
    job_button,
    jobs_page,
    navigate,
    open_job,
    planned_start,
    settle,
    started,
    wait,
)
from support.gui_flow import FlowRecorder, gui_exchanges, match_steps
from support.launch_legs import LaunchLegs
from transcribe_ui.gui.cleanup.view import CleanupDialog
from transcribe_ui.gui.constants import PAGE_NEW_JOB, PAGE_SETTINGS
from transcribe_ui.gui.dialogs import ConfirmationDialog
from transcribe_ui.gui.download.constants import STRATEGY_CHOICES
from transcribe_ui.gui.download.install import DownloadArea
from transcribe_ui.gui.download.view import DownloadDialog
from transcribe_ui.gui.jobs.constants import JobButtonKey
from transcribe_ui.gui.review.view import ReviewPage
from transcribe_ui.gui.selection.models import SelectionTreeModel
from transcribe_ui.gui.selection.view import NewJobPage
from transcribe_ui.gui.settings.view import SettingsPage
from transcribe_ui.gui.shell import MainWindow

# Files of the media folder (the job's files).
MEDIA_FILES = 4
# The `refresh_status` steps of the CLI flow, each one interval of the poll.
REFRESH_STEPS = ("refresh_started", "refresh_transcribing", "refresh_finished")


@dataclass
class GuiFlow:
    """The window over the GUI leg, its recorder, its leg (cloud and bucket) and the poll's manual timer."""

    window: MainWindow
    recorder: FlowRecorder
    legs: LaunchLegs
    root: Path
    timers: ManualTimers


def _english_first(qtbot: QtBot, window: MainWindow, page: SettingsPage) -> None:
    """Preferences: untick Hungarian and tick it again (a language ticked again goes last); Save."""
    page.setCurrentWidget(page.preferences)
    table = page.preferences.fields.languages
    hungarian = table.table.item(table.row_of(HU), 0)
    assert hungarian is not None
    hungarian.setCheckState(Qt.CheckState.Unchecked)
    hungarian.setCheckState(Qt.CheckState.Checked)
    with qtbot.waitSignal(window.context.signals.settings_changed, timeout=WAIT_MS):
        page.preferences.controls.save_button.click()
    saved = page.models.preferences.shown.saved
    assert saved is not None
    assert saved.enabled_languages[:2] == (EN, HU)


def _settings(qtbot: QtBot, window: MainWindow) -> None:
    """Settings: English before Hungarian in the preferences, Test the accounts, Check the cloud setup."""
    navigate(qtbot, window, PAGE_SETTINGS)
    page = window.pages.page(PAGE_SETTINGS)
    assert isinstance(page, SettingsPage)
    _english_first(qtbot, window, page)
    page.setCurrentWidget(page.accounts)
    controls = page.accounts.controls
    controls.test_button.click()
    wait(qtbot, controls.checks_label.text)
    page.setCurrentWidget(page.cloud_setup)
    page.cloud_setup.controls.check_button.click()
    settle(qtbot, window)


def _row(tree: SelectionTreeModel, parent: QModelIndex, name: str) -> QModelIndex:
    for row in range(tree.rowCount(parent)):
        index = tree.index(row, 0, parent)
        if tree.data(index) == name:
            return index
    pytest.fail(f"no row {name}")


def _choose_folder(qtbot: QtBot, page: NewJobPage, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Choose folder (the system dialog answers `folder`); tick the scanned folder."""
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_arguments: str(folder))
    page.folder_bar.choose_button.click()
    tree = page.tree_area.tree_model
    wait(qtbot, lambda: tree.rowCount() == 1)
    assert tree.setData(tree.index(0, 0), Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole)
    wait(qtbot, lambda: page.footer.totals.text().startswith(f"{MEDIA_FILES} files selected"))


def _silent_in_hungarian(qtbot: QtBot, page: NewJobPage) -> None:
    """Select the silent video in the tree; untick "Same as the job", then English."""
    tree = page.tree_area.tree_model
    silent = _row(tree, _row(tree, tree.index(0, 0), "Movies"), SILENT)
    page.tree_area.tree.setCurrentIndex(silent)
    languages = page.tree_area.file_languages
    wait(qtbot, languages.isVisible)
    languages.same.setChecked(False)
    languages.checks.boxes[EN].click()
    assert languages.checks.ticked() == (HU,)


def _create_confirmed(qtbot: QtBot, window: MainWindow, page: NewJobPage) -> None:
    """Create job; the film has an English subtitle, so the backend asks first: Confirm."""
    footer = page.footer
    settle(qtbot, window, footer.save_button.isEnabled)
    footer.save_button.click()
    wait(qtbot, lambda: isinstance(window.dialog, ConfirmationDialog))
    dialog = window.dialog
    assert isinstance(dialog, ConfirmationDialog)
    with qtbot.waitSignal(window.context.signals.open_job, timeout=WAIT_MS):
        dialog.confirm_button.click()
    settle(qtbot, window)


def _new_job(qtbot: QtBot, flow: GuiFlow, monkeypatch: pytest.MonkeyPatch) -> None:
    """New job: the media folder in English and Hungarian, the silent video in Hungarian only."""
    window = flow.window
    navigate(qtbot, window, PAGE_NEW_JOB)
    page = window.pages.page(PAGE_NEW_JOB)
    assert isinstance(page, NewJobPage)
    boxes = page.footer.languages.boxes
    wait(qtbot, lambda: EN in boxes)
    _choose_folder(qtbot, page, media_dir(flow.root), monkeypatch)
    boxes[EN].click()
    boxes[HU].click()
    _silent_in_hungarian(qtbot, page)
    _create_confirmed(qtbot, window, page)


def _review_and_upload(qtbot: QtBot, window: MainWindow) -> None:
    """The job page: Continue to review; the review page checks the files; Upload."""
    job_button(qtbot, window, FIRST_BATCH_ID, "continue_review").click()
    jobs = jobs_page(window)
    wait(qtbot, lambda: isinstance(jobs.currentWidget(), ReviewPage))
    review = jobs.currentWidget()
    assert isinstance(review, ReviewPage)
    upload = review.action_row.upload_button
    settle(qtbot, window, upload.isEnabled)
    before = entries(window)
    upload.click()
    finished_after(qtbot, window, before, "run_upload")


def _start(qtbot: QtBot, window: MainWindow) -> None:
    """The start dialog's Start launches the worker; the run shows on the Cloud page."""
    dialog = planned_start(qtbot, window)
    dialog.start_button.click()
    runs = cloud_page(window).history.runs
    settle(qtbot, window, lambda: not dialog.isVisible() and runs.run_active())


def _poll(qtbot: QtBot, flow: GuiFlow, step: str) -> None:
    """The simulated worker changes the cloud as before the CLI flow's step; then the poll's interval passes."""
    change = WORKER_CHANGES.get(step)
    if change is not None:
        matched = match_steps(flow.recorder.answered(), complete=False)
        change_legs(flow.legs, change, gui_exchanges(matched, flow.root))
    timer = flow.timers.poll
    assert timer.active, f"the poll is not running before {step}"
    timer.fire()
    settle(qtbot, flow.window)


def _download_dialog(qtbot: QtBot, window: MainWindow) -> DownloadDialog:
    """The job page's Download; return its dialog once the plan of the default choices can download."""
    open_job(qtbot, window, FIRST_BATCH_ID)
    job_button(qtbot, window, FIRST_BATCH_ID, "download").click()
    area = window.findChild(DownloadArea)
    assert isinstance(area, DownloadArea)
    wait(qtbot, lambda: FIRST_BATCH_ID in area.dialogs)
    dialog = area.dialogs[FIRST_BATCH_ID]
    wait(qtbot, dialog.download_button.isEnabled)
    return dialog


def _mirror_into(qtbot: QtBot, dialog: DownloadDialog, folder: Path) -> None:
    """Choose mirror; type the output folder and Enter."""
    dialog.options.strategy.setCurrentIndex(STRATEGY_CHOICES.index("mirror"))
    field = dialog.options.output_folder
    wait(qtbot, field.isEnabled)
    field.setFocus()
    QTest.keyClicks(field, str(folder))
    QTest.keyClick(field, Qt.Key.Key_Return)


def _download(qtbot: QtBot, flow: GuiFlow) -> None:
    """Download: mirror into the typed output folder; Close."""
    window = flow.window
    dialog = _download_dialog(qtbot, window)
    _mirror_into(qtbot, dialog, flow.root / "out")
    settle(qtbot, window, dialog.download_button.isEnabled)
    dialog.download_button.click()
    settle(qtbot, window, lambda: dialog.run_panel.outcome.text().startswith("Last download"))
    dialog.reject()


def _retry(qtbot: QtBot, window: MainWindow) -> str:
    """Retry the ticked transcriptions (the failed and the empty one); return the retry job the page then shows."""
    open_job(qtbot, window, FIRST_BATCH_ID)
    button = job_button(qtbot, window, FIRST_BATCH_ID, "retry")
    before = entries(window)
    button.click()
    finished_after(qtbot, window, before, "create_retry_batch")
    model = jobs_page(window).page_view.model
    settle(qtbot, window, lambda: model.batch_id not in {None, FIRST_BATCH_ID})
    retry_id = model.batch_id
    assert retry_id is not None
    return retry_id


def _clean_up(qtbot: QtBot, window: MainWindow, batch_id: str, key: JobButtonKey) -> None:
    """The job page's cleanup button; the question's delete button."""
    open_job(qtbot, window, batch_id)
    button = job_button(qtbot, window, batch_id, key)
    settle(qtbot, window)
    before = entries(window)
    button.click()
    wait(qtbot, lambda: isinstance(QApplication.activeModalWidget(), CleanupDialog))
    question = QApplication.activeModalWidget()
    assert isinstance(question, CleanupDialog)
    question.delete_button.click()
    finished_after(qtbot, window, before, "cleanup_cloud")


def _to_the_end_of_the_run(qtbot: QtBot, flow: GuiFlow, monkeypatch: pytest.MonkeyPatch) -> None:
    """From the start-up to the run's end: settings, the new job, review and upload, the start, the poll."""
    window = flow.window
    started(qtbot, window)
    _settings(qtbot, window)
    _new_job(qtbot, flow, monkeypatch)
    _review_and_upload(qtbot, window)
    _start(qtbot, window)
    for step in REFRESH_STEPS:
        _poll(qtbot, flow, step)
    assert not flow.timers.poll.active, "the poll goes on after the run ended"


def drive(qtbot: QtBot, flow: GuiFlow, monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive the window through the flow, as a user would."""
    window = flow.window
    _to_the_end_of_the_run(qtbot, flow, monkeypatch)
    _download(qtbot, flow)
    retry_id = _retry(qtbot, window)
    # The retry job's plan (the CLI flow's `plan_retry`); Close.
    planned_start(qtbot, window).reject()
    _clean_up(qtbot, window, FIRST_BATCH_ID, "cleanup_audio")
    _clean_up(qtbot, window, FIRST_BATCH_ID, "cleanup_everything")
    _clean_up(qtbot, window, retry_id, "cleanup_everything")
