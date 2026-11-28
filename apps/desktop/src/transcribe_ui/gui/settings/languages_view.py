"""The settings page's language list: every Whisper language with an on/off box and its word timing mark."""

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from transcribe_ui.api.classes import LanguageOption
from transcribe_ui.gui.settings.constants import (
    ALIGNMENT_COLUMN,
    CODE_COLUMN,
    LANGUAGE_COLUMNS,
    LANGUAGE_TABLE_HEIGHT,
    NAME_COLUMN,
)
from transcribe_ui.i18n.catalog import tr

# Every Whisper language as the settings page lists it.
type Languages = tuple[LanguageOption, ...]
# Told that the user turned a language on (True) or off (False).
type Toggle = Callable[[str, bool], None]


def _cell(text: str) -> QTableWidgetItem:
    cell = QTableWidgetItem(text)
    cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    return cell


def _name_cell(language: LanguageOption) -> QTableWidgetItem:
    cell = _cell(language.name)
    cell.setFlags(cell.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    # A cell shows its box only once it has a check state.
    cell.setCheckState(Qt.CheckState.Unchecked)
    cell.setData(Qt.ItemDataRole.UserRole, language.code)
    return cell


def _check_state(*, ticked: bool) -> Qt.CheckState:
    return Qt.CheckState.Checked if ticked else Qt.CheckState.Unchecked


def _table(parent: QWidget) -> QTableWidget:
    table = QTableWidget(0, LANGUAGE_COLUMNS, parent)
    headers = [tr("Language"), tr("Code"), tr("Word timing")]
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(True)
    table.setMinimumHeight(LANGUAGE_TABLE_HEIGHT)
    return table


def _matches(language: LanguageOption, wanted: str) -> bool:
    searched = (language.name.casefold(), language.code.casefold())
    return any(wanted in text for text in searched)


def _listed(languages: Languages) -> list[tuple[str, str, bool]]:
    return [(language.code, language.name, language.alignment) for language in languages]


class LanguageTable(QWidget):
    """A filter field and the table of languages; the boxes follow the form's enabled languages."""

    def __init__(self, on_toggle: Toggle, parent: QWidget | None = None) -> None:
        """Tell `on_toggle` when the user ticks or unticks a language."""
        super().__init__(parent)
        self._on_toggle = on_toggle
        self._languages: Languages = ()
        self._painting = False
        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText(tr("Filter languages"))
        self.filter_edit.textChanged.connect(self._filter)
        self.table = _table(self)
        self.table.itemChanged.connect(self._changed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.filter_edit)
        layout.addWidget(self.table)

    def show_languages(self, languages: Languages, enabled: tuple[str, ...]) -> None:
        """Show `languages` (rebuilt only when they change) with the boxes of `enabled` ticked."""
        self._painting = True
        # Rebuilt only when the list itself changes, not when only `enabled` does (a save).
        if _listed(languages) != _listed(self._languages):
            self._languages = languages
            self._rebuild()
        for row, language in enumerate(languages):
            cell = self.table.item(row, NAME_COLUMN)
            state = _check_state(ticked=language.code in enabled)
            if cell is not None and cell.checkState() != state:
                cell.setCheckState(state)
        self._painting = False

    def row_of(self, code: str) -> int:
        """Return the table row of a language code (-1 when it is not listed)."""
        codes = [language.code for language in self._languages]
        return codes.index(code) if code in codes else -1

    def _rebuild(self) -> None:
        self.table.setRowCount(len(self._languages))
        word_timing = tr("yes")
        for row, language in enumerate(self._languages):
            alignment = word_timing if language.alignment else ""
            self.table.setItem(row, NAME_COLUMN, _name_cell(language))
            self.table.setItem(row, CODE_COLUMN, _cell(language.code))
            self.table.setItem(row, ALIGNMENT_COLUMN, _cell(alignment))
        self.table.resizeColumnsToContents()
        self._filter(self.filter_edit.text())

    def _changed(self, cell: QTableWidgetItem) -> None:
        if self._painting or cell.column() != NAME_COLUMN:
            return
        ticked = cell.checkState() == Qt.CheckState.Checked
        self._on_toggle(str(cell.data(Qt.ItemDataRole.UserRole)), ticked)

    def _filter(self, typed: str) -> None:
        wanted = typed.strip().casefold()
        for row, language in enumerate(self._languages):
            self.table.setRowHidden(row, not _matches(language, wanted))
