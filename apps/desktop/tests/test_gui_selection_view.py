"""The New job page in the window and the selection tree's item model (pytest-qt, offscreen; plan m6 M6.4).

The tree model passes Qt's `QAbstractItemModelTester` on a 20 000-file scan (its structure:
the tester walks the whole model at each signal) and while ticks, filters, languages and a new
scan change it on the small media tree; a tick tells the view only about the rows it changed.
In the window, Choose folder scans, a tick box click previews, a language and
Create job create the job and open its page; "Edit selection" on a job page reopens it; and a
new job is scanned and previewed while the long pool is full of uploads.
"""

from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtTest import QAbstractItemModelTester
from PySide6.QtWidgets import QFileDialog, QWidget
from pytestqt.qtbot import QtBot

from support.discovery import FILM, PODCAST, media_dir
from support.gated import wait_for
from support.gui import qt_messages
from support.selection import HeldUploads, media_path, media_ports, selection_area
from support.selection_window import ChangedSpans, released, settled_window
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.ports import Ports
from transcribe_ui.batches.classes import CreateBatchRequest
from transcribe_ui.batches.responses import CreateBatchResponse
from transcribe_ui.discovery.classes import ScanSpec, SelectionOperation
from transcribe_ui.gui.constants import LONG_POOL_THREADS, PAGE_JOBS, PAGE_NEW_JOB
from transcribe_ui.gui.dialogs import ConfirmationDialog
from transcribe_ui.gui.selection.choices import LanguageChoice
from transcribe_ui.gui.selection.filters import FilterInput
from transcribe_ui.gui.selection.models import SelectionTreeModel
from transcribe_ui.gui.selection.view import NewJobPage
from transcribe_ui.gui.selection.widgets import FileLanguages, LanguageChecks
from transcribe_ui.gui.shell import MainWindow
from transcribe_ui.upload.classes import RunUploadRequest

ENGLISH = "en"
HUNGARIAN = "hu"
ENGLISH_ONLY = (ENGLISH,)
# The file the languages panel shows, and the languages it offers.
CLIP = "/media/clip.mkv"
CLIP_NAME = "clip.mkv"
OFFERED = (LanguageChoice(ENGLISH, "English (en)"), LanguageChoice(HUNGARIAN, "Hungarian (hu)"))
BATCH_ID = "batch_20261127_120000"
# Uploads that fill the long pool, and one more that waits for a thread.
UPLOADS = LONG_POOL_THREADS + 1
# A folder of the large tree with its clips.
LARGE_FOLDER = "folder007"
# Folders of the large tree, and clips in each.
LARGE_FOLDERS = 200
LARGE_CLIPS = 100
# A clip of that folder and its row.
LARGE_CLIP = "clip003.mkv"
LARGE_CLIP_ROW = 3
# Files of the Audio folder.
AUDIO_FILES = 2
# Media files directly in the Movies folder.
MOVIES_FILES = 4
# Most `dataChanged` signals a tick may send (the click's row, then up to three ranges of the answer).
TICK_SIGNALS = 4
# The tree's last column, and the column of the target languages.
LAST_COLUMN = 4
TARGETS_COLUMN = 4


@pytest.fixture
def ports(tmp_path: Path) -> Iterator[Ports]:
    """Return seeded ports with the media tree (closed afterwards)."""
    created = media_ports(tmp_path)
    with closing(created.state):
        yield created


@pytest.fixture
def window(qtbot: QtBot, ports: Ports, tmp_path: Path) -> MainWindow:
    """Return the window over the seeded backend, its start-up steps run."""
    created = settled_window(qtbot, Backend(ports), tmp_path)
    created.context.hooks.run()
    return created


def _page(window: MainWindow) -> NewJobPage:
    page = window.pages.page(PAGE_NEW_JOB)
    assert isinstance(page, NewJobPage)
    return page


def _row(model: SelectionTreeModel, parent: QModelIndex, name: str) -> QModelIndex:
    for row in range(model.rowCount(parent)):
        index = model.index(row, 0, parent)
        if model.data(index) == name:
            return index
    pytest.fail(f"no row {name}")


def _check(model: SelectionTreeModel, index: QModelIndex | QPersistentModelIndex) -> object:
    return model.data(index, Qt.ItemDataRole.CheckStateRole)


def _choose(monkeypatch: pytest.MonkeyPatch, folder: Path) -> None:
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_arguments: str(folder))


def _attached(tree: SelectionTreeModel) -> QAbstractItemModelTester:
    return QAbstractItemModelTester(tree, QAbstractItemModelTester.FailureReportingMode.Warning)


@pytest.mark.usefixtures("qapp")
def test_the_model_tester_on_the_large_scan(large_tree: Path, tmp_path: Path) -> None:
    """Qt's model tester walks the whole tree of a 20 000-file scan and finds nothing; rows and parents agree."""
    ports = media_ports(tmp_path / "state")
    with qt_messages() as collected, closing(ports.state):
        model = selection_area(Backend(ports)).model
        model.choose_folder(str(large_tree))
        tree = SelectionTreeModel(model)
        tester = _attached(tree)
        root = tree.index(0, 0)
        folder = _row(tree, root, LARGE_FOLDER)
        clip = tree.index(LARGE_CLIP_ROW, 0, folder)
    assert (tree.rowCount(), tree.rowCount(root), tree.rowCount(folder)) == (1, LARGE_FOLDERS, LARGE_CLIPS)
    assert (tree.data(clip), tree.parent(clip), tester.model()) == (LARGE_CLIP, folder, tree)
    assert not collected.messages


@pytest.mark.usefixtures("qapp")
def test_the_model_tester_while_the_selection_changes(ports: Ports, tmp_path: Path) -> None:
    """Every change the page makes (ticks, filters, languages, a new scan) passes Qt's model tester.

    The tester walks the whole model at each signal, so the changes run on the small media tree.
    """
    with qt_messages() as collected:
        model = selection_area(Backend(ports)).model
        tree = SelectionTreeModel(model)
        tester = _attached(tree)
        model.choose_folder(media_path(tmp_path))
        audio = _row(tree, tree.index(0, 0), "Audio")
        model.tick(media_path(tmp_path, "Audio"), folder=True)
        model.tick(media_path(tmp_path, "Audio", PODCAST), folder=False)
        model.change_filters(FilterInput(media_kind="video"))
        model.targets.use_languages(ENGLISH_ONLY)
        marks = (_check(tree, tree.index(0, 0)), _check(tree, audio), tester.model() is tree)
        model.choose_folder(media_path(tmp_path, "Movies"))
    assert marks == (Qt.CheckState.PartiallyChecked, Qt.CheckState.PartiallyChecked, True)
    assert (collected.messages, tree.rowCount(tree.index(0, 0))) == ([], MOVIES_FILES)


def _chosen(qtbot: QtBot, page: NewJobPage, folder: Path, monkeypatch: pytest.MonkeyPatch) -> SelectionTreeModel:
    """Wait for the settings, then Choose folder (the dialog answers `folder`) and wait for the tree."""
    wait_for(qtbot, lambda: ENGLISH in page.footer.languages.boxes)
    _choose(monkeypatch, folder)
    page.folder_bar.choose_button.click()
    tree = page.tree_area.tree_model
    wait_for(qtbot, lambda: tree.rowCount() == 1)
    return tree


def test_choose_tick_and_create(
    qtbot: QtBot,
    window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Choose folder scans; a click on a tick box selects; a language enables Create job, which opens the job."""
    page = _page(window)
    footer = page.footer
    assert (window.pages.current_page(), footer.save_button.isEnabled()) == (PAGE_NEW_JOB, False)
    tree = _chosen(qtbot, page, media_dir(tmp_path), monkeypatch)
    audio = _row(tree, tree.index(0, 0), "Audio")
    podcast = _row(tree, audio, PODCAST)
    assert tree.setData(podcast, Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole)
    wait_for(qtbot, lambda: footer.totals.text().startswith("1 file selected"))
    assert (_check(tree, podcast), _check(tree, audio)) == (Qt.CheckState.Checked, Qt.CheckState.PartiallyChecked)
    footer.languages.boxes[ENGLISH].click()
    assert footer.save_button.isEnabled()
    with qtbot.waitSignal(window.context.signals.open_job) as opened:
        footer.save_button.click()
    assert opened.args == [BATCH_ID]
    wait_for(qtbot, lambda: window.pages.current_page() == PAGE_JOBS)
    wait_for(qtbot, lambda: footer.totals.text() == "Nothing is selected.")


def _job_of_movies(ports: Ports, root: Path) -> None:
    """Create the job of every film in Movies, in English (confirmed), as another frontend would."""
    operation = SelectionOperation(operation="include", path=media_path(root, "Movies"))
    request = CreateBatchRequest(
        action="create_batch",
        scan=ScanSpec(root=media_path(root)),
        operations=(operation,),
        languages=ENGLISH_ONLY,
        confirm_existing_languages=True,
    )
    assert isinstance(Backend(ports).execute(request), CreateBatchResponse)


def _confirm(qtbot: QtBot, window: MainWindow) -> list[object]:
    """Confirm the open confirmation dialog; return the arguments of the `open_job` that follows."""
    wait_for(qtbot, lambda: isinstance(window.dialog, ConfirmationDialog))
    dialog = window.dialog
    assert isinstance(dialog, ConfirmationDialog)
    blocker = qtbot.waitSignal(window.context.signals.open_job)
    with blocker:
        dialog.confirm_button.click()
    return list(blocker.args or [])


def test_edit_selection_reopens_the_job(qtbot: QtBot, window: MainWindow, ports: Ports, tmp_path: Path) -> None:
    """Edit selection on a job page opens New job on the job's stored selection, saved with Save selection."""
    _job_of_movies(ports, tmp_path)
    window.context.signals.edit_selection.emit(BATCH_ID)
    page = _page(window)
    tree = page.tree_area.tree_model
    wait_for(qtbot, lambda: page.footer.totals.text().startswith("4 files selected"))
    shown = (window.pages.current_page(), page.header.title.text(), page.footer.save_button.text())
    assert shown == (PAGE_NEW_JOB, f"Editing the selection of job {BATCH_ID}", "Save selection")
    assert page.header.new_button.isVisibleTo(page)
    film = _row(tree, _row(tree, tree.index(0, 0), "Movies"), FILM)
    tree.setData(film, Qt.CheckState.Unchecked.value, Qt.ItemDataRole.CheckStateRole)
    wait_for(qtbot, lambda: page.footer.totals.text().startswith("3 files selected"))
    page.footer.save_button.click()
    # The other films have an English subtitle: the backend asks first.
    assert _confirm(qtbot, window) == [BATCH_ID]
    wait_for(qtbot, lambda: page.header.title.text() == "New job")


def _held_uploads(window: MainWindow) -> list[object]:
    """Send uploads that fill the long pool (one more waits); return the list their answers go to."""
    answered: list[object] = []
    for index in range(UPLOADS):
        request = RunUploadRequest(action="run_upload", batch_id=f"batch_20261127_1200{index:02d}")
        window.context.executor.submit(request, answered.append)
    return answered


def test_a_new_job_while_uploads_run(qtbot: QtBot, ports: Ports, tmp_path: Path) -> None:
    """With the long pool full of uploads (and one waiting), New job still scans and previews."""
    runner = HeldUploads(Backend(ports))
    window = settled_window(qtbot, runner, tmp_path)
    with released(runner):
        answered = _held_uploads(window)
        wait_for(qtbot, lambda: runner.held == LONG_POOL_THREADS)
        model = _page(window).model
        model.choose_folder(media_path(tmp_path))
        wait_for(qtbot, lambda: model.ticks.counts is not None)
        model.tick(media_path(tmp_path, "Audio"), folder=True)
        wait_for(qtbot, lambda: model.ticks.counts is not None and model.ticks.counts.files == AUDIO_FILES)
        assert (runner.held, answered) == (LONG_POOL_THREADS, [])


@pytest.mark.usefixtures("qapp")
def test_a_tick_repaints_only_what_it_changed(large_tree: Path, tmp_path: Path) -> None:
    """On the 200-folder tree a tick sends a few `dataChanged` for the rows it changed, not one per folder."""
    ports = media_ports(tmp_path / "state")
    with closing(ports.state):
        model = selection_area(Backend(ports)).model
        model.choose_folder(str(large_tree))
        tree = SelectionTreeModel(model)
        recorder = ChangedSpans()
        tree.dataChanged.connect(recorder)
        spans = recorder.spans
        layouts: list[bool] = []
        tree.layoutChanged.connect(lambda: layouts.append(True))
        model.tick(str(large_tree / LARGE_FOLDER), folder=True)
        ticked = list(spans)
        spans.clear()
        model.targets.use_languages(ENGLISH_ONLY)
        languages = list(spans)
        model.change_filters(FilterInput(media_kind="audio"))
    # The click's row, then the answer: the folder's clips, the folder's row, the root's row.
    assert len(ticked) <= TICK_SIGNALS < LARGE_FOLDERS
    assert (LARGE_CLIPS, 0, LAST_COLUMN) in ticked
    assert languages == [(LARGE_CLIPS, TARGETS_COLUMN, TARGETS_COLUMN)]
    assert layouts == [True], "turning a filter on may dim any row: one layout change"


@dataclass
class Panel:
    """The file languages panel in its holder widget (kept alive with it), and the changes it reported."""

    holder: QWidget
    widget: FileLanguages
    received: list[object]


def _panel(qtbot: QtBot, job: tuple[str, ...]) -> Panel:
    """Return the file languages panel (its changes recorded) for a job on `job`."""
    received: list[object] = []
    holder = QWidget()
    qtbot.addWidget(holder)
    panel = FileLanguages(received.append, holder)
    panel.use_job_languages(job)
    return Panel(holder, panel, received)


def _show(panel: FileLanguages, own: tuple[str, ...] | None, job: tuple[str, ...] = ENGLISH_ONLY) -> None:
    """Repaint the panel for the clip as the page does: the job's languages, then the file."""
    panel.use_job_languages(job)
    panel.show_file(CLIP, CLIP_NAME, OFFERED, own)


def test_own_languages_without_job_languages(qtbot: QtBot) -> None:
    """With no job language ticked, unticking "Same as the job" stays unticked until a language is ticked."""
    shown = _panel(qtbot, ())
    panel, received = shown.widget, shown.received
    _show(panel, None, ())
    assert (panel.same.isChecked(), panel.checks.isEnabled()) == (True, False)
    panel.same.setChecked(False)
    # The page repaints from the model, which still has no own languages for the file.
    _show(panel, None, ())
    assert (panel.same.isChecked(), panel.checks.isEnabled(), received) == (False, True, [None])
    panel.checks.boxes[ENGLISH].click()
    panel.show_file("/media/other.mkv", "other.mkv", OFFERED, None)
    assert (received, panel.same.isChecked()) == ([None, ENGLISH_ONLY], True)


def test_same_as_the_job_returns_with_a_language(qtbot: QtBot) -> None:
    """Choosing own languages with no job language, then giving the job one, shows "Same as the job" again."""
    shown = _panel(qtbot, ())
    panel = shown.widget
    _show(panel, None, ())
    panel.same.setChecked(False)
    _show(panel, None)
    assert (panel.same.isChecked(), panel.checks.ticked(), panel.checks.isEnabled()) == (True, ENGLISH_ONLY, False)


def test_the_job_languages_follow_the_offered_order(qtbot: QtBot) -> None:
    """The job's languages come in the order offered (the settings' order), whatever order they were ticked in."""
    received: list[tuple[str, ...]] = []
    holder = QWidget()
    qtbot.addWidget(holder)
    checks = LanguageChecks(received.append, holder)
    checks.show_languages(OFFERED, ())
    checks.boxes[HUNGARIAN].click()
    checks.boxes[ENGLISH].click()
    assert received == [(HUNGARIAN,), (ENGLISH, HUNGARIAN)]


def test_the_last_own_language_can_go(qtbot: QtBot) -> None:
    """With the job on English, a file switching to Hungarian may drop English first without snapping back."""
    shown = _panel(qtbot, ENGLISH_ONLY)
    panel, received = shown.widget, shown.received
    _show(panel, None)
    panel.same.setChecked(False)
    _show(panel, ENGLISH_ONLY)
    panel.checks.boxes[ENGLISH].click()
    # The model drops the empty own languages; the page repaints.
    _show(panel, None)
    assert (panel.same.isChecked(), panel.checks.isEnabled(), panel.checks.ticked()) == (False, True, ())
    panel.checks.boxes[HUNGARIAN].click()
    assert received == [ENGLISH_ONLY, None, (HUNGARIAN,)]
