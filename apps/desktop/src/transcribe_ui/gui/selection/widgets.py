"""Widgets of the New job page: language tick boxes and menus, the filter bar, a file's own languages, warnings.

They hold no rule: each shows display data and passes the user's input to a callback.
"""

from collections.abc import Callable, Iterable

from PySide6.QtCore import QSignalBlocker, QTimer
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from transcribe_ui.discovery.constants import NAME_FILTER_MAX_LENGTH
from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.selection.choices import LanguageChoice
from transcribe_ui.gui.selection.constants import LANGUAGE_SEPARATOR, NAME_FILTER_DELAY_MS, Languages
from transcribe_ui.gui.selection.filters import FilterInput
from transcribe_ui.i18n.catalog import tr

# Told the languages ticked, in the order offered.
type OnLanguages = Callable[[Languages], None]
# Told a file's own languages (None: the job's).
type OnOwnLanguages = Callable[[Languages | None], None]
# The languages a chooser offers.
type Offered = tuple[LanguageChoice, ...]
# The choices of a combo box: the value it stands for and its label.
type Choices = tuple[tuple[str, str], ...]


def add_all(layout: QBoxLayout, widgets: Iterable[QWidget]) -> None:
    """Add the widgets to the layout in order."""
    for widget in widgets:
        layout.addWidget(widget)


def line_of(widgets: Iterable[QWidget], *, stretch: bool = False) -> QHBoxLayout:
    """Return a row of the widgets (ending in a stretch when `stretch`), to add to another layout."""
    line = QHBoxLayout()
    add_all(line, widgets)
    if stretch:
        line.addStretch()
    return line


def plain_box(parent: QWidget, layout: type[QHBoxLayout | QVBoxLayout] = QHBoxLayout) -> QBoxLayout:
    """Return a layout of `parent` without margins."""
    created = layout(parent)
    created.setContentsMargins(0, 0, 0, 0)
    return created


class LanguageChecks(QWidget):
    """One tick box per language offered."""

    def __init__(self, on_change: OnLanguages, parent: QWidget) -> None:
        """Start without languages; a click calls `on_change` with the ticked ones."""
        super().__init__(parent)
        self._layout = plain_box(self)
        self._on_change = on_change
        self.boxes: dict[str, QCheckBox] = {}
        self._offered: tuple[LanguageChoice, ...] = ()

    def show_languages(self, offered: Offered, ticked: Languages) -> None:
        """Offer these languages (rebuilt only when they change) with `ticked` ticked."""
        if offered != self._offered:
            self._rebuild(offered)
        for code, box in self.boxes.items():
            with QSignalBlocker(box):
                box.setChecked(code in ticked)

    def ticked(self) -> tuple[str, ...]:
        """Return the ticked languages in the order offered."""
        boxes = self.boxes.items()
        return tuple(code for code, box in boxes if box.isChecked())

    def _rebuild(self, offered: tuple[LanguageChoice, ...]) -> None:
        self._offered = offered
        for old in self.boxes.values():
            old.deleteLater()
        self.boxes = {}
        for choice in offered:
            box = QCheckBox(choice.label, self)
            box.toggled.connect(self._toggled)
            self._layout.addWidget(box)
            self.boxes[choice.code] = box

    def _toggled(self) -> None:
        self._on_change(self.ticked())


class LanguageMenu(QToolButton):
    """A button whose menu ticks languages (the subtitle filter's); its text names the ticked ones."""

    def __init__(self, on_change: Callable[[], None], parent: QWidget) -> None:
        """Start without languages; a tick calls `on_change`."""
        super().__init__(parent)
        self._on_change = on_change
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setMenu(QMenu(self))
        self._offered: tuple[LanguageChoice, ...] | None = None
        self.show_languages(())

    def show_languages(self, offered: tuple[LanguageChoice, ...]) -> None:
        """Offer these languages (the ticked ones still offered stay ticked)."""
        if offered == self._offered:
            return
        self._offered = offered
        ticked = set(self.ticked())
        self.menu().clear()
        for choice in offered:
            action = self.menu().addAction(choice.label)
            action.setCheckable(True)
            action.setChecked(choice.code in ticked)
            action.setData(choice.code)
            action.toggled.connect(self._toggled)
        self._label()

    def ticked(self) -> tuple[str, ...]:
        """Return the ticked languages in the order offered."""
        actions = self.menu().actions()
        ticked = [action for action in actions if action.isChecked()]
        return tuple(str(action.data()) for action in ticked)

    def clear(self) -> None:
        """Untick every language (without calling back)."""
        for action in self.menu().actions():
            with QSignalBlocker(action):
                action.setChecked(False)
        self._label()

    def _label(self) -> None:
        chosen = LANGUAGE_SEPARATOR.join(self.ticked())
        text = tr("Subtitles: any")
        if chosen:
            text = tr("Subtitles: {languages}").format(languages=chosen)
        self.setText(text)

    def _toggled(self) -> None:
        self._label()
        self._on_change()


def _kind_choices() -> Choices:
    return (
        ("any", tr("Audio and video")),
        ("audio", tr("Audio only")),
        ("video", tr("Video only")),
    )


def _rule_choices() -> Choices:
    return (
        ("any", tr("has any of them")),
        ("all", tr("has all of them")),
        ("missing_one", tr("is missing at least one")),
    )


def _name_choices() -> Choices:
    return (
        ("contains", tr("contains")),
        ("starts_with", tr("starts with")),
        ("ends_with", tr("ends with")),
        ("regex", tr("matches the regular expression")),
    )


def combo_box(choices: Choices, parent: QWidget) -> QComboBox:
    """Return a combo box of these choices (each item's data is its value)."""
    combo = QComboBox(parent)
    for key, label in choices:
        combo.addItem(label, key)
    return combo


class FilterBar(QWidget):
    """Media kind, subtitle languages with their rule, and the name filter; a change calls back at once.

    The name filter calls back `NAME_FILTER_DELAY_MS` after the last keystroke.
    """

    def __init__(self, on_change: Callable[[FilterInput], None], parent: QWidget) -> None:
        """Start without filters."""
        super().__init__(parent)
        self._on_change = on_change
        self.kind = combo_box(_kind_choices(), self)
        self.subtitles = LanguageMenu(self._changed, self)
        self.rule = combo_box(_rule_choices(), self)
        self.name_match = combo_box(_name_choices(), self)
        self.name_text = self._name_field()
        self.note = wrapped_label("", self)
        self._timer = self._delay()
        self._layout_bar()
        self._enable_rule()

    def picked(self) -> FilterInput:
        """Return the bar's values."""
        return FilterInput(
            media_kind=self.kind.currentData(),
            subtitle_languages=self.subtitles.ticked(),
            subtitle_rule=self.rule.currentData(),
            name_match=self.name_match.currentData(),
            name_text=self.name_text.text(),
        )

    def show_state(self, problem: str | None, *, active: bool) -> None:
        """Show why the filters cannot be used, or that folder ticks follow them."""
        note = ""
        if problem is not None:
            note = problem
        elif active:
            note = tr("Filters are on: ticking a folder selects only its matching files; the others are dimmed.")
        self.note.setText(note)

    def clear(self) -> None:
        """Remove every filter."""
        for combo in (self.kind, self.rule, self.name_match):
            with QSignalBlocker(combo):
                combo.setCurrentIndex(0)
        self.subtitles.clear()
        with QSignalBlocker(self.name_text):
            self.name_text.clear()
        self._changed()

    def _layout_bar(self) -> None:
        rows = plain_box(self, QVBoxLayout)
        line = QHBoxLayout()
        rows.addLayout(line)
        clear_button = QPushButton(tr("Clear filters"), self)
        clear_button.clicked.connect(self.clear)
        shown = QLabel(tr("Filters:"), self)
        add_all(line, (shown, self.kind, self.subtitles, self.rule))
        named = QLabel(tr("Name"), self)
        add_all(line, (named, self.name_match, self.name_text, clear_button))
        rows.addWidget(self.note)
        for combo in (self.kind, self.rule, self.name_match):
            combo.currentIndexChanged.connect(self._changed)

    def _delay(self) -> QTimer:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(NAME_FILTER_DELAY_MS)
        timer.timeout.connect(self._changed)
        return timer

    def _name_field(self) -> QLineEdit:
        field = QLineEdit(self)
        field.setPlaceholderText(tr("File name"))
        field.setMaxLength(NAME_FILTER_MAX_LENGTH)
        field.setClearButtonEnabled(True)
        field.textChanged.connect(self._typed)
        return field

    def _typed(self) -> None:
        self._timer.start()

    def _enable_rule(self) -> None:
        self.rule.setEnabled(bool(self.subtitles.ticked()))

    def _changed(self) -> None:
        self._timer.stop()
        self._enable_rule()
        self._on_change(self.picked())


class FileLanguages(QWidget):
    """The languages of the file selected in the tree: the job's, or its own."""

    def __init__(self, on_change: OnOwnLanguages, parent: QWidget) -> None:
        """Hidden until a selected file is shown; a change calls `on_change` (None: the job's languages)."""
        super().__init__(parent)
        self._on_change = on_change
        self.title = QLabel(self)
        self.same = QCheckBox(tr("Same as the job"), self)
        self.same.toggled.connect(self._same_toggled)
        self.checks = LanguageChecks(self._picked, self)
        layout = plain_box(self)
        add_all(layout, (self.title, self.same, self.checks))
        layout.addStretch()
        self._job: tuple[str, ...] = ()
        self.setVisible(False)

    def use_job_languages(self, job: tuple[str, ...]) -> None:
        """Take the job's languages, which a file without its own is transcribed into."""
        self._job = job

    def show_file(self, name: str | None, offered: Offered, own: Languages | None) -> None:
        """Show a selected file's languages (`own` None: the job's), or hide the panel (`name` None)."""
        self.setVisible(name is not None)
        if name is None:
            return
        self.title.setText(tr("Languages of {name}:").format(name=name))
        with QSignalBlocker(self.same):
            self.same.setChecked(own is None)
        self.checks.show_languages(offered, self._job if own is None else own)
        self.checks.setEnabled(own is not None)

    def _same_toggled(self, same: bool) -> None:  # noqa: FBT001 -- a Qt slot gets the state positionally
        self._on_change(None if same else self._job)

    def _picked(self, languages: tuple[str, ...]) -> None:
        self._on_change(languages or None)


class WarningsBox(QWidget):
    """The scan's and the selection's warnings, collapsed behind a button; hidden while there are none."""

    def __init__(self, parent: QWidget) -> None:
        """Start hidden."""
        super().__init__(parent)
        self.toggle = QToolButton(self)
        self.toggle.setCheckable(True)
        self.table = QTreeWidget(self)
        self.table.setHeaderLabels([tr("Warning"), tr("Path")])
        self.table.setRootIsDecorated(False)
        self.toggle.toggled.connect(self.table.setVisible)
        add_all(plain_box(self, QVBoxLayout), (self.toggle, self.table))
        self._rows: tuple[tuple[str, str], ...] = ()
        self.table.setVisible(False)
        self.setVisible(False)

    def show_rows(self, rows: tuple[tuple[str, str], ...]) -> None:
        """Show the warnings (text and path each)."""
        self.setVisible(bool(rows))
        if rows == self._rows:
            return
        self._rows = rows
        count = len(rows)
        self.toggle.setText(tr("Warnings ({count})").format(count=count))
        self.table.clear()
        self.table.addTopLevelItems([QTreeWidgetItem(list(row)) for row in rows])
