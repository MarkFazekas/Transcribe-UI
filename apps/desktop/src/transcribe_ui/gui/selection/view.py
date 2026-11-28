"""The New job page: step 1 of a job (folder, tree, filters, selection, languages; architecture §3.1 "GUI").

Binds the widgets to `NewJobModel` and decides nothing: the tree's tick boxes and the totals
come from the latest preview, every text from the display helpers, and a click calls the
view-model. The folder dialog is injected, so tests choose a folder without one.
"""

from collections.abc import Callable
from functools import partial

from PySide6.QtCore import QDir, QModelIndex, QPersistentModelIndex
from PySide6.QtWidgets import QFileDialog, QHeaderView, QLabel, QPushButton, QTreeView, QVBoxLayout, QWidget

from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.jobs.widgets import ErrorBanner
from transcribe_ui.gui.selection.constants import NAME_COLUMN
from transcribe_ui.gui.selection.display import save_hint, scan_text, totals_text, warning_rows
from transcribe_ui.gui.selection.models import SelectionTreeModel
from transcribe_ui.gui.selection.viewmodel import NewJobModel
from transcribe_ui.gui.selection.widgets import (
    FileLanguages,
    FilterBar,
    LanguageChecks,
    WarningsBox,
    add_all,
    line_of,
    plain_box,
)
from transcribe_ui.gui.steps.view import StepHeader
from transcribe_ui.i18n.catalog import tr

# Asks the user for a folder (the parent of the dialog, the folder it starts in); "" when cancelled.
type ChooseFolder = Callable[[QWidget, str], str]

# Width of the tree's name column, in pixels.
NAME_COLUMN_WIDTH = 360


def choose_folder_dialog(parent: QWidget, start: str) -> str:
    """Ask for a folder with the system's dialog; return it with the system's separators ("" when cancelled)."""
    chosen = QFileDialog.getExistingDirectory(parent, tr("Choose a folder to scan"), start)
    return QDir.toNativeSeparators(chosen) if chosen else ""


def _button(text: str, on_click: Callable[[], object], parent: QWidget) -> QPushButton:
    """Return a button that calls `on_click` without the `checked` argument."""
    button = QPushButton(text, parent)
    button.clicked.connect(partial(_call, on_click))
    return button


def _call(on_click: Callable[[], object], *_ignored: object) -> None:
    on_click()


class JobHeader(QWidget):
    """The step header, the page's title, "Start a new job instead" while editing, and a settings error."""

    def __init__(self, model: NewJobModel, parent: QWidget) -> None:
        """Bind to `model`."""
        super().__init__(parent)
        self.steps = StepHeader(self)
        self.title = QLabel(self)
        self.new_button = _button(tr("Start a new job instead"), model.start_new, self)
        self.settings_error = ErrorBanner(model.settings.load, self)
        rows = plain_box(self, QVBoxLayout)
        rows.addWidget(self.steps)
        rows.addLayout(line_of((self.title, self.new_button), stretch=True))
        rows.addWidget(self.settings_error)

    def refresh(self, model: NewJobModel) -> None:
        """Show the job's step and whether a new job is made or an existing one edited."""
        editing = model.saver.editing
        self.steps.show_stage(None if editing is None else editing.stage, "files")
        title = tr("New job")
        if editing is not None:
            title = tr("Editing the selection of job {batch_id}").format(batch_id=editing.batch_id)
        self.title.setText(title)
        self.new_button.setVisible(editing is not None)
        self.settings_error.show_error(model.settings.error)


class FolderBar(QWidget):
    """Choose folder, Scan again, Stop scan, the folder shown and the scan's state."""

    def __init__(self, model: NewJobModel, choose: ChooseFolder, parent: QWidget) -> None:
        """Bind to `model`; `choose` asks for the folder."""
        super().__init__(parent)
        self._model = model
        self._choose = choose
        self.choose_button = _button(tr("Choose folder…"), self._choose_folder, self)
        self.scan_again_button = _button(tr("Scan again"), self._scan_again, self)
        self.stop_button = _button(tr("Stop scan"), model.scan.cancel, self)
        self.folder = QLabel(self)
        self.status = wrapped_label("", self)
        self.error = ErrorBanner(self._scan_again, self)
        rows = plain_box(self, QVBoxLayout)
        buttons = (self.choose_button, self.scan_again_button, self.stop_button)
        rows.addLayout(line_of((*buttons, self.folder)))
        add_all(rows, (self.status, self.error))

    def refresh(self) -> None:
        """Show the folder, the scan's state and its error."""
        scan = self._model.scan
        spec = scan.spec
        scanning = scan.status == "scanning"
        self.folder.setText("" if spec is None else spec.root)
        self.scan_again_button.setEnabled(spec is not None and not scanning)
        self.stop_button.setVisible(scanning)
        counts = None if scan.scanned is None else scan.scanned.index.tree.counts
        self.status.setText(scan_text(scan.status, scan.found, counts))
        self.error.show_error(scan.error)

    def _choose_folder(self) -> None:
        spec = self._model.scan.spec
        chosen = self._choose(self, "" if spec is None else spec.root)
        if chosen:
            self._model.choose_folder(chosen)

    def _scan_again(self) -> None:
        spec = self._model.scan.spec
        if spec is not None:
            self._model.scan.start(spec)


class TreeArea(QWidget):
    """The tree, the languages of the file selected in it, the warnings and the selection's error."""

    def __init__(self, model: NewJobModel, parent: QWidget) -> None:
        """Bind to `model`."""
        super().__init__(parent)
        self._model = model
        self.tree_model = SelectionTreeModel(model, self)
        self.tree = self._tree()
        self.file_languages = FileLanguages(self._own_languages, self)
        self.warnings = WarningsBox(self)
        self.error = ErrorBanner(model.ticks.preview, self)
        rows = plain_box(self, QVBoxLayout)
        add_all(rows, (self.tree, self.file_languages, self.warnings, self.error))
        rows.setStretch(0, 1)

    def refresh(self) -> None:
        """Show the warnings, the selection's error and the current file's languages."""
        model = self._model
        scanned = model.scan.scanned
        found = () if scanned is None else scanned.index.tree.warnings
        warnings = (*found, *model.ticks.warnings)
        self.warnings.show_rows(warning_rows(warnings))
        self.error.show_error(model.ticks.error or model.filters.error)
        self.file_languages.use_job_languages(model.targets.languages)
        self._show_file(self.tree.currentIndex())

    def _tree(self) -> QTreeView:
        tree = QTreeView(self)
        # Every row has the same height, so the view never measures rows it does not show.
        tree.setUniformRowHeights(True)
        tree.setModel(self.tree_model)
        tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        tree.header().resizeSection(NAME_COLUMN, NAME_COLUMN_WIDTH)
        self.tree_model.modelReset.connect(self._expand_root)
        tree.selectionModel().currentChanged.connect(self._show_file)
        return tree

    def _expand_root(self) -> None:
        self.tree.expand(self.tree_model.index(0, 0))

    def _show_file(self, current: QModelIndex | QPersistentModelIndex) -> None:
        model = self._model
        scan_index = self.tree_model.scan_index
        scanned = None
        if scan_index is not None and current.isValid():
            scanned = scan_index.media_file(current.internalId())
        if scanned is None or scanned.path not in model.ticks.marks.files:
            self.file_languages.show_file(None, (), None)
            return
        own = model.targets.own(scanned.path)
        shown = model.targets.languages + (own or ())
        offered = model.settings.languages(shown)
        self.file_languages.show_file(scanned.name, offered, own)

    def _own_languages(self, languages: tuple[str, ...] | None) -> None:
        scan_index = self.tree_model.scan_index
        current = self.tree.currentIndex()
        if scan_index is not None and current.isValid():
            self._model.targets.use_own(scan_index.path(current.internalId()), languages)


class Footer(QWidget):
    """The job's languages, the totals, why the job cannot be created yet, Clear selection and Create job."""

    def __init__(self, model: NewJobModel, parent: QWidget) -> None:
        """Bind to `model`."""
        super().__init__(parent)
        self.languages = LanguageChecks(model.targets.use_languages, self)
        self.totals = QLabel(self)
        self.hint = QLabel(self)
        self.clear_button = _button(tr("Clear selection"), model.ticks.clear, self)
        self.save_button = _button(tr("Create job"), model.save, self)
        rows = plain_box(self, QVBoxLayout)
        label = QLabel(tr("Transcribe into:"), self)
        rows.addLayout(line_of((label, self.languages), stretch=True))
        rows.addWidget(self.totals)
        rows.addLayout(line_of((self.hint, self.clear_button, self.save_button)))

    def refresh(self, model: NewJobModel) -> None:
        """Show the languages, the totals and whether the job can be created."""
        targets = model.targets
        self.languages.show_languages(model.settings.languages(targets.languages), targets.languages)
        self.totals.setText(totals_text(model.ticks.counts))
        hint = save_hint(model)
        self.hint.setText(hint or "")
        self.clear_button.setEnabled(bool(model.ticks.operations))
        new = model.saver.editing is None
        self.save_button.setText(tr("Create job") if new else tr("Save selection"))
        self.save_button.setEnabled(hint is None)


class NewJobPage(QWidget):
    """Step 1 of a job: the folder, the filters, the tree, the languages, the totals and Create job."""

    def __init__(
        self,
        model: NewJobModel,
        parent: QWidget | None = None,
        choose: ChooseFolder = choose_folder_dialog,
    ) -> None:
        """Bind to `model`; `choose` asks for the folder (the system's dialog by default)."""
        super().__init__(parent)
        self.model = model
        self.header = JobHeader(model, self)
        self.folder_bar = FolderBar(model, choose, self)
        self.filter_bar = FilterBar(model.change_filters, self)
        self.tree_area = TreeArea(model, self)
        self.footer = Footer(model, self)
        rows = QVBoxLayout(self)
        top = (self.header, self.folder_bar, self.filter_bar)
        add_all(rows, (*top, self.tree_area, self.footer))
        rows.setStretch(3, 1)
        model.subscribe(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        """Show the model's state."""
        model = self.model
        self.header.refresh(model)
        self.folder_bar.refresh()
        filters = model.filters
        self.filter_bar.show_state(filters.problem, active=filters.picked.active())
        self.filter_bar.subtitles.show_languages(model.settings.languages())
        self.tree_area.refresh()
        self.footer.refresh(model)
