"""The choice panels of the download dialog: strategy and options, the folders kept in the names, the custom plan.

They bind to the view-model and decide nothing: a change of a widget becomes a changed form
(`DownloadModel.edit`), and the widgets show the model's form. A widget is set only when the
form changed since it was last shown, so a repaint never undoes what the user is typing.
"""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.download import constants
from transcribe_ui.gui.download.chooser import FileChooser
from transcribe_ui.gui.download.display import strategy_text
from transcribe_ui.gui.download.form import DownloadForm
from transcribe_ui.gui.download.state import DialogData
from transcribe_ui.gui.download.viewmodel import DownloadModel
from transcribe_ui.gui.download.widgets import show_text
from transcribe_ui.i18n.catalog import tr

# A folder level to choose: its number and its text.
type LevelChoice = tuple[int, str]


class FolderNames(QWidget):
    """The folders `flat_with_folders` joins into the names: the closest ones (a count), or chosen levels."""

    def __init__(self, model: DownloadModel, parent: QWidget) -> None:
        """Bind to `model`."""
        super().__init__(parent)
        self._model = model
        self.count_button = QRadioButton(tr("The closest folders:"), self)
        self.count = QSpinBox(self)
        self.levels_button = QRadioButton(tr("These folder levels, counted from the scanned folder:"), self)
        self.levels = QListWidget(self)
        self._connect()
        self._lay_out()
        self._shown: tuple[DownloadForm, tuple[LevelChoice, ...]] | None = None
        self._levels: tuple[LevelChoice, ...] | None = None

    def show_form(self, form: DownloadForm, levels: tuple[LevelChoice, ...]) -> None:
        """Show the form's folder choice and the levels to choose from (only when they changed)."""
        if self._shown != (form, levels):
            self._shown = (form, levels)
            self._show_mode(form)
            self._show_levels(form, levels)

    def _connect(self) -> None:
        self.count.setRange(1, constants.MAX_FOLDER_COUNT)
        self.count_button.toggled.connect(self._mode_changed)
        self.count.valueChanged.connect(self._count_changed)
        self.levels.itemChanged.connect(self._level_changed)

    def _lay_out(self) -> None:
        closest = QHBoxLayout()
        closest.addWidget(self.count_button)
        closest.addWidget(self.count)
        closest.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(closest)
        layout.addWidget(self.levels_button)
        layout.addWidget(self.levels)

    def _show_mode(self, form: DownloadForm) -> None:
        by_levels = form.folder_mode == constants.FOLDER_LEVELS_MODE
        with QSignalBlocker(self.count_button), QSignalBlocker(self.levels_button), QSignalBlocker(self.count):
            self.count_button.setChecked(not by_levels)
            self.levels_button.setChecked(by_levels)
            self.count.setValue(form.folder_count)
        self.count.setEnabled(not by_levels)
        self.levels.setEnabled(by_levels)

    def _show_levels(self, form: DownloadForm, levels: tuple[LevelChoice, ...]) -> None:
        """Rebuild the list only when the levels change; otherwise update the ticks in place (scroll kept)."""
        with QSignalBlocker(self.levels):
            if levels != self._levels:
                self._levels = levels
                self.levels.clear()
                for level, text in levels:
                    entry = QListWidgetItem(text, self.levels)
                    entry.setData(Qt.ItemDataRole.UserRole, level)
                    entry.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            for row in range(self.levels.count()):
                self._tick(self.levels.item(row), form)

    def _tick(self, entry: QListWidgetItem, form: DownloadForm) -> None:
        ticked = int(entry.data(Qt.ItemDataRole.UserRole)) in form.folder_levels
        entry.setCheckState(Qt.CheckState.Checked if ticked else Qt.CheckState.Unchecked)

    def _mode_changed(self) -> None:
        by_count = self.count_button.isChecked()
        self._model.edit(folder_mode=constants.FOLDER_COUNT_MODE if by_count else constants.FOLDER_LEVELS_MODE)

    def _count_changed(self, count: int) -> None:
        self._model.edit(folder_count=count)

    def _level_changed(self, entry: QListWidgetItem) -> None:
        level = int(entry.data(Qt.ItemDataRole.UserRole))
        chosen = set(self._model.form.folder_levels)
        if entry.checkState() == Qt.CheckState.Checked:
            chosen.add(level)
        else:
            chosen.discard(level)
        self._model.edit(folder_levels=frozenset(chosen))


class OptionsPanel(QWidget):
    """The strategy, the output folder, the folders kept in the names, and the outcome options."""

    def __init__(self, model: DownloadModel, chooser: FileChooser, parent: QWidget) -> None:
        """Bind to `model`; "Choose…" asks `chooser` for the output folder."""
        super().__init__(parent)
        self._model = model
        self._chooser = chooser
        self.strategy = QComboBox(self)
        self.output_folder = QLineEdit(self)
        self.folder_names = FolderNames(model, self)
        self.overwrite = QCheckBox(tr("Replace existing subtitle files"), self)
        self.include_empty = QCheckBox(tr("Also place empty subtitles (no speech was found)"), self)
        self._layout = QFormLayout(self)
        self._folder = self._folder_row()
        self._connect()
        self._add_rows()
        self._shown: DownloadForm | None = None

    def show_form(self, form: DownloadForm, levels: tuple[LevelChoice, ...]) -> None:
        """Show the form's choices (only what changed since the last time) and the rows that apply to its strategy."""
        self.folder_names.show_form(form, levels)
        if form == self._shown:
            return
        self._shown = form
        with QSignalBlocker(self.strategy), QSignalBlocker(self.overwrite), QSignalBlocker(self.include_empty):
            self.strategy.setCurrentIndex(constants.STRATEGY_CHOICES.index(form.strategy))
            self.overwrite.setChecked(form.overwrite)
            self.include_empty.setChecked(form.include_empty)
        if self.output_folder.text() != form.output_folder:
            self.output_folder.setText(form.output_folder)
        self._layout.setRowVisible(self.folder_names, form.strategy == constants.FLAT_WITH_FOLDERS)
        self._folder.setEnabled(form.strategy != constants.NEXT_TO_MEDIA)

    def _connect(self) -> None:
        for choice in constants.STRATEGY_CHOICES:
            self.strategy.addItem(strategy_text(choice), choice)
        self.strategy.currentIndexChanged.connect(self._strategy_changed)
        self.output_folder.editingFinished.connect(self._folder_typed)
        self.overwrite.toggled.connect(self._overwrite_toggled)
        self.include_empty.toggled.connect(self._empty_toggled)

    def _add_rows(self) -> None:
        rows = (
            (tr("Place the subtitles:"), self.strategy),
            (tr("Output folder:"), self._folder),
            (tr("Folder names in the file names:"), self.folder_names),
        )
        for label, widget in rows:
            self._layout.addRow(label, widget)
        self._layout.addRow(self.overwrite)
        self._layout.addRow(self.include_empty)

    def _folder_row(self) -> QWidget:
        folder = QWidget(self)
        row = QHBoxLayout(folder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.output_folder, 1)
        choose = QPushButton(tr("Choose…"), folder)
        choose.clicked.connect(self._choose_folder)
        row.addWidget(choose)
        return folder

    def _overwrite_toggled(self, checked: bool) -> None:  # noqa: FBT001 -- a Qt slot gets the state positionally
        self._model.edit(overwrite=checked)

    def _empty_toggled(self, checked: bool) -> None:  # noqa: FBT001 -- a Qt slot gets the state positionally
        self._model.edit(include_empty=checked)

    def _strategy_changed(self, index: int) -> None:
        self._model.edit(strategy=constants.STRATEGY_CHOICES[index])

    def _folder_typed(self) -> None:
        self._model.edit(output_folder=self.output_folder.text().strip())

    def _choose_folder(self) -> None:
        start = self._model.form.output_folder or str(Path.home())
        chosen = self._chooser.folder(self, start)
        if chosen is not None:
            self.output_folder.setText(chosen)
            self._model.edit(output_folder=chosen)


class CustomPanel(QWidget):
    """The custom plan: export it to a file, import the edited file, check it again after more edits."""

    def __init__(self, model: DownloadModel, chooser: FileChooser, parent: QWidget) -> None:
        """Bind to `model`; the file dialogs come from `chooser`."""
        super().__init__(parent)
        self._model = model
        self._chooser = chooser
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        buttons = QHBoxLayout()
        self.export_button = self._button(tr("Export plan…"), self._export)
        self.import_button = self._button(tr("Import plan…"), self._import)
        self.check_button = self._button(tr("Check again"), self._check)
        for button in (self.export_button, self.import_button, self.check_button):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self._exported = wrapped_label("", self)
        layout.addWidget(self._exported)
        self._imported = wrapped_label("", self)
        layout.addWidget(self._imported)

    def show_files(self, shown: DialogData) -> None:
        """Show where the plan was exported to and which file was imported."""
        show_text(self._exported, shown.exported)
        show_text(self._imported, shown.imported)
        self.check_button.setEnabled(bool(shown.imported))

    def _button(self, text: str, action: Callable[[], None]) -> QPushButton:
        button = QPushButton(text, self)
        button.clicked.connect(action)
        return button

    def _folder(self) -> Path:
        return Path(self._model.form.output_folder or Path.home())

    def _export(self) -> None:
        name = constants.PLAN_FILE_NAME.format(batch_id=self._model.batch_id)
        start = self._folder() / name
        chosen = self._chooser.save_plan(self, str(start))
        if chosen is not None:
            self._model.export.export(chosen)

    def _import(self) -> None:
        earlier = self._model.plan_path or self._model.export.path
        start = earlier or str(self._folder())
        chosen = self._chooser.open_plan(self, start)
        if chosen is not None:
            self._model.import_plan(chosen)

    def _check(self) -> None:
        self._model.reload(batch=False)
