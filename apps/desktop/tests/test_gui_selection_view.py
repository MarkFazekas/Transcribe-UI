"""The New job page in the window and the selection tree's item model (pytest-qt, offscreen; plan m6 M6.4).

The tree model passes Qt's `QAbstractItemModelTester` on a 20 000-file scan, also while ticks
change it. In the window, Choose folder scans, a tick box click previews, a language and
Create job create the job and open its page; "Edit selection" on a job page reopens it; and a
new job is scanned and previewed while the long pool is full of uploads.
"""

from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from PySide6.QtCore import QMessageLogContext, QModelIndex, QPersistentModelIndex, Qt, QtMsgType, qInstallMessageHandler
from PySide6.QtTest import QAbstractItemModelTester
from PySide6.QtWidgets import QFileDialog
from pytestqt.qtbot import QtBot

from support.discovery import FILM, PODCAST, media_dir
from support.gated import wait_for
from support.gui import window_over
from support.selection import HeldUploads, media_path, media_ports, selection_area
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.ports import Ports
from transcribe_ui.batches.classes import CreateBatchRequest
from transcribe_ui.batches.responses import CreateBatchResponse
from transcribe_ui.discovery.classes import ScanSpec, SelectionOperation
from transcribe_ui.gui.constants import LONG_POOL_THREADS, PAGE_JOBS, PAGE_NEW_JOB
from transcribe_ui.gui.dialogs import ConfirmationDialog
from transcribe_ui.gui.selection.filters import FilterInput
from transcribe_ui.gui.selection.models import SelectionTreeModel
from transcribe_ui.gui.selection.view import NewJobPage
from transcribe_ui.gui.shell import MainWindow
from transcribe_ui.upload.classes import RunUploadRequest

ENGLISH = "en"
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


@pytest.fixture
def ports(tmp_path: Path) -> Iterator[Ports]:
    """Return seeded ports with the media tree (closed afterwards)."""
    created = media_ports(tmp_path)
    with closing(created.state):
        yield created


def _drained(qtbot: QtBot, window: MainWindow) -> None:
    wait_for(qtbot, lambda: not window.busy())
    # Answers still on their way must not reach the widgets pytest-qt deletes next.
    window.context.executor.close()


@pytest.fixture
def window(qtbot: QtBot, ports: Ports, tmp_path: Path) -> Iterator[MainWindow]:
    """Return the window over the seeded backend, its start-up steps run; drained afterwards."""
    created = window_over(qtbot, Backend(ports), tmp_path)
    created.context.hooks.run()
    yield created
    _drained(qtbot, created)


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


@dataclass
class QtMessages:
    """Collects the messages Qt reports (the model tester reports its failures as warnings)."""

    messages: list[str] = field(default_factory=list)

    def __call__(self, kind: QtMsgType, _context: QMessageLogContext, message: str) -> None:
        """Keep every message above debug."""
        if kind != QtMsgType.QtDebugMsg:
            self.messages.append(message)


@contextmanager
def qt_messages() -> Iterator[QtMessages]:
    """Collect what Qt reports while the block runs (the handler before it is restored afterwards)."""
    collected = QtMessages()
    previous = qInstallMessageHandler(collected)
    try:
        yield collected
    finally:
        qInstallMessageHandler(previous)


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
        model.targets.use_languages((ENGLISH,))
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
    # pytest-qt deletes the window before the fixture drains it: settle here.
    _drained(qtbot, window)


def _job_of_movies(ports: Ports, root: Path) -> None:
    """Create the job of every film in Movies, in English (confirmed), as another frontend would."""
    operation = SelectionOperation(operation="include", path=media_path(root, "Movies"))
    request = CreateBatchRequest(
        action="create_batch",
        scan=ScanSpec(root=media_path(root)),
        operations=(operation,),
        languages=(ENGLISH,),
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
    # pytest-qt deletes the window before the fixture drains it: settle here.
    _drained(qtbot, window)


def test_a_new_job_while_uploads_run(qtbot: QtBot, ports: Ports, tmp_path: Path) -> None:
    """With the long pool full of uploads (and one waiting), New job still scans and previews."""
    runner = HeldUploads(Backend(ports))
    window = window_over(qtbot, runner, tmp_path)
    executor = window.context.executor
    uploads = [
        RunUploadRequest(action="run_upload", batch_id=f"batch_20261127_1200{index:02d}") for index in range(UPLOADS)
    ]
    answered: list[object] = []
    for request in uploads:
        executor.submit(request, answered.append)
    wait_for(qtbot, lambda: runner.held == LONG_POOL_THREADS)
    model = _page(window).model
    model.choose_folder(media_path(tmp_path))
    wait_for(qtbot, lambda: model.ticks.counts is not None)
    model.tick(media_path(tmp_path, "Audio"), folder=True)
    wait_for(qtbot, lambda: model.ticks.counts is not None and model.ticks.counts.files == AUDIO_FILES)
    assert (runner.held, answered) == (LONG_POOL_THREADS, [])
    runner.release()
    _drained(qtbot, window)
