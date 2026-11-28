"""The download dialog in the window (pytest-qt, offscreen; plan m6 M6.9).

The window runs over the seeded `transcribed` batch through `QtExecutor`: `open_download` opens
the dialog with its plan, the widgets reach their requests, Download runs in the activity
panel, the overwrite question is the backend's confirmation, and a custom plan goes through the
file dialogs (answered by a fake).
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot

from support.batches import FIRST_BATCH_ID, media_dir
from support.download import FILM_ID, seed_download_batch, write_subtitles
from support.download_gui import Recording
from support.gated import wait_for
from support.gui import window_over
from support.monitor import SUBTITLE
from support.runs import RunsWorld
from transcribe_contracts.layout import result_srt_key
from transcribe_ui.gui.dialogs import ConfirmationDialog
from transcribe_ui.gui.download.constants import STRATEGY_CHOICES
from transcribe_ui.gui.download.install import DownloadArea
from transcribe_ui.gui.download.view import DownloadDialog
from transcribe_ui.gui.shell import MainWindow
from transcribe_ui.placement.classes import PlanDownloadRequest

# Jobs of the seeded batch (one row each in the plan table).
JOBS = 4
# Column of the plan table that says what happens.
OUTCOME_COLUMN = 4


@dataclass
class FakeChooser:
    """Answers the file dialogs with fixed paths and records what was asked."""

    folder_answer: str | None = None
    save_answer: str | None = None
    open_answer: str | None = None
    asked: list[str] = field(default_factory=list)

    def folder(self, _parent: object, start: str) -> str | None:
        """Return the chosen output folder."""
        self.asked.append(start)
        return self.folder_answer

    def save_plan(self, _parent: object, start: str) -> str | None:
        """Return the file to export to."""
        self.asked.append(start)
        return self.save_answer

    def open_plan(self, _parent: object, start: str) -> str | None:
        """Return the file to import."""
        self.asked.append(start)
        return self.open_answer


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The runs world (moto, every credential) with the transcribed batch and its subtitles."""
    seed_download_batch(runs_world.ports.state, runs_world.leg.root, "transcribed")
    write_subtitles(runs_world.store)
    return runs_world


@pytest.fixture
def window(qtbot: QtBot, world: RunsWorld, tmp_path: Path) -> Iterator[MainWindow]:
    """Return the window over the seeded backend.

    pytest-qt closes the window before the fixtures' teardown, so each test waits for its answers
    itself (`_settle`); answers still on their way after that are dropped here.
    """
    created = window_over(qtbot, world.leg.backend, tmp_path)
    yield created
    created.context.executor.close()


@pytest.fixture
def chooser(window: MainWindow) -> FakeChooser:
    """Answer the dialog's file dialogs with a fake."""
    area = window.findChild(DownloadArea)
    assert isinstance(area, DownloadArea)
    fake = FakeChooser()
    area.chooser = fake
    return fake


def _dialog(window: MainWindow) -> DownloadDialog:
    area = window.findChild(DownloadArea)
    assert isinstance(area, DownloadArea)
    return area.dialogs[FIRST_BATCH_ID]


def _open(qtbot: QtBot, window: MainWindow) -> DownloadDialog:
    window.context.signals.open_download.emit(FIRST_BATCH_ID)
    dialog = _dialog(window)
    wait_for(qtbot, lambda: dialog.plan_panel.table.rowCount() == JOBS and dialog.download_button.isEnabled())
    return dialog


def _settle(qtbot: QtBot, window: MainWindow) -> None:
    """Wait for every answer, before pytest-qt deletes the window (ahead of the fixtures' teardown)."""
    wait_for(qtbot, lambda: not window.busy())


def _outcomes(dialog: DownloadDialog) -> list[str]:
    table = dialog.plan_panel.table
    cells = (table.item(row, OUTCOME_COLUMN) for row in range(table.rowCount()))
    return [cell.text() for cell in cells if cell is not None]


def _button(dialog: DownloadDialog, text: str) -> QPushButton:
    found = [button for button in dialog.findChildren(QPushButton) if button.text() == text]
    assert len(found) == 1, text
    return found[0]


def test_open_download_shows_the_plan(qtbot: QtBot, window: MainWindow) -> None:
    """`open_download` opens the job's dialog at step 4 with the plan of the default choices; again, the same one."""
    dialog = _open(qtbot, window)
    shown = (dialog.isVisible(), dialog.options.output_folder.isEnabled(), _outcomes(dialog)[0])
    assert shown == (True, False, "Skipped: the file already exists")
    dialog.reject()
    assert not dialog.isVisible()
    before = dialog.model.loaded
    assert _open(qtbot, window) is dialog
    wait_for(qtbot, lambda: dialog.model.loaded is not before and not dialog.model.loaded.loading)
    assert _outcomes(dialog)[0] == "Skipped: the file already exists"
    _settle(qtbot, window)


def test_enter_in_the_output_folder_only_ends_typing(
    qtbot: QtBot,
    window: MainWindow,
    chooser: FakeChooser,
    world: RunsWorld,
) -> None:
    """Enter takes the typed folder; it neither opens the folder chooser nor closes the dialog."""
    out = str(world.leg.root / "out")
    dialog = _open(qtbot, window)
    dialog.options.strategy.setCurrentIndex(STRATEGY_CHOICES.index("mirror"))
    field = dialog.options.output_folder
    wait_for(qtbot, field.isEnabled)
    field.setFocus()
    QTest.keyClicks(field, out)
    QTest.keyClick(field, Qt.Key.Key_Return)
    wait_for(qtbot, dialog.download_button.isEnabled)
    assert (chooser.asked, dialog.isVisible(), dialog.model.form.output_folder) == ([], True, out)
    # No button becomes the default of the dialog, whichever has the focus when it shows.
    buttons = dialog.findChildren(QPushButton)
    assert not any(button.autoDefault() for button in buttons)
    _settle(qtbot, window)


def test_mirror_downloads_in_the_activity_panel(
    qtbot: QtBot,
    window: MainWindow,
    chooser: FakeChooser,
    world: RunsWorld,
) -> None:
    """Mirror with a chosen folder downloads in the activity panel; the dialog shows the outcome."""
    out = world.leg.root / "out"
    chooser.folder_answer = str(out)
    dialog = _open(qtbot, window)
    _choose_mirror(qtbot, dialog)
    assert dialog.options.output_folder.text() == str(out)
    dialog.download_button.click()
    wait_for(qtbot, lambda: dialog.run_panel.outcome.text().startswith("Last download: 2 written"))
    assert (out / "Movies" / "Film.en.srt").read_bytes() == SUBTITLE
    assert [row.entry.status for row in window.activity_panel.rows] == ["Finished"]
    _settle(qtbot, window)


def test_overwrite_asks_over_the_dialog(qtbot: QtBot, window: MainWindow, world: RunsWorld) -> None:
    """Replacing a file asks the backend's question over the dialog; Confirm replaces it."""
    sidecar = media_dir(world.leg.root) / "Movies" / "Film.en.srt"
    dialog = _open(qtbot, window)
    dialog.options.overwrite.setChecked(True)
    wait_for(qtbot, lambda: _outcomes(dialog)[:1] == ["Will replace the existing file"])
    wait_for(qtbot, dialog.download_button.isEnabled)
    dialog.download_button.click()
    wait_for(qtbot, lambda: dialog.findChild(ConfirmationDialog) is not None)
    question = dialog.findChild(ConfirmationDialog)
    assert isinstance(question, ConfirmationDialog)
    question.confirm_button.click()
    wait_for(qtbot, lambda: sidecar.read_bytes() == SUBTITLE)
    _settle(qtbot, window)


def _choose_mirror(qtbot: QtBot, dialog: DownloadDialog) -> None:
    """Choose mirror, then the output folder the chooser answers; wait for its plan."""
    dialog.options.strategy.setCurrentIndex(STRATEGY_CHOICES.index("mirror"))
    wait_for(qtbot, lambda: not dialog.download_button.isEnabled())
    _button(dialog, "Choose…").click()
    wait_for(qtbot, dialog.download_button.isEnabled)


def _type_custom_folder(qtbot: QtBot, dialog: DownloadDialog, folder: Path) -> None:
    """Choose the custom plan and type its output folder."""
    dialog.options.strategy.setCurrentIndex(STRATEGY_CHOICES.index("custom"))
    dialog.options.output_folder.setText(str(folder))
    dialog.options.output_folder.editingFinished.emit()
    wait_for(qtbot, dialog.custom.isVisible)


def test_a_custom_plan_through_the_file_dialogs(
    qtbot: QtBot,
    window: MainWindow,
    chooser: FakeChooser,
    world: RunsWorld,
) -> None:
    """Export and import go through the file dialogs; the checked plan fills the table and Download places it."""
    plan_file = world.leg.root / "plan.json"
    custom = world.leg.root / "custom"
    chooser.save_answer = str(plan_file)
    chooser.open_answer = chooser.save_answer
    dialog = _open(qtbot, window)
    _type_custom_folder(qtbot, dialog, custom)
    dialog.custom.export_button.click()
    wait_for(qtbot, plan_file.exists)
    assert chooser.asked == [str(custom / f"subtitle-plan-{FIRST_BATCH_ID}.json")]
    dialog.custom.import_button.click()
    wait_for(qtbot, lambda: dialog.plan_panel.table.rowCount() == JOBS and dialog.download_button.isEnabled())
    dialog.download_button.click()
    wait_for(qtbot, (custom / "Movies" / "Film.en.srt").exists)
    _settle(qtbot, window)


def test_failures_show_the_closed_dialog_again(
    qtbot: QtBot,
    window: MainWindow,
    chooser: FakeChooser,
    world: RunsWorld,
) -> None:
    """A download that could not place a file shows its dialog again, if it was closed meanwhile, with the failure."""
    world.store.delete(result_srt_key(FIRST_BATCH_ID, FILM_ID, "en"))
    chooser.folder_answer = str(world.leg.root / "out")
    dialog = _open(qtbot, window)
    _choose_mirror(qtbot, dialog)
    dialog.download_button.click()
    dialog.reject()
    wait_for(qtbot, lambda: dialog.isVisible() and dialog.run_panel.failures.rowCount() == 1)
    _settle(qtbot, window)


def test_count_steps_plan_once_after_the_delay(qtbot: QtBot, world: RunsWorld, tmp_path: Path) -> None:
    """Two quick steps of the folder count send one `plan_download`, for the last count, once the delay ends."""
    runner = Recording(world.leg.backend)
    window = window_over(qtbot, runner, tmp_path)
    window.context.signals.open_download.emit(FIRST_BATCH_ID)
    dialog = _dialog(window)
    dialog.model.edit(strategy="flat_with_folders", output_folder=str(world.leg.root / "out"))
    wait_for(qtbot, lambda: dialog.model.loaded.view is not None and not dialog.model.loaded.loading)
    sent = len(runner.requests)
    dialog.options.folder_names.count.stepUp()
    dialog.options.folder_names.count.stepUp()
    wait_for(qtbot, lambda: not dialog.model.loaded.loading)
    _settle(qtbot, window)
    plans = [request for request in runner.requests[sent:] if isinstance(request, PlanDownloadRequest)]
    assert [plan.placement.folder_count for plan in plans] == [3]
    window.context.executor.close()
