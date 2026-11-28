"""The accounts section: a masked field per credential, where each is set, Save and Test (architecture §3.6).

Fields never show a stored value (none ever reaches the GUI): they start empty with a
placeholder, show what is typed masked, and are cleared once it was stored.
"""

from dataclasses import dataclass
from functools import partial

from PySide6.QtWidgets import QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from transcribe_ui.credentials.constants import CREDENTIAL_ORDER, R2_CREDENTIALS
from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.settings.account_texts import credential_label, keyring_note, runpod_optional_note
from transcribe_ui.gui.settings.accounts import AccountRow, AccountsState, AccountsViewModel, CheckRow
from transcribe_ui.gui.settings.constants import FORM_FIELD, OPTIONAL_CREDENTIAL
from transcribe_ui.gui.settings.widgets import error_line, show_text
from transcribe_ui.i18n.catalog import tr


@dataclass(frozen=True)
class CredentialField:
    """The widgets of one credential."""

    edit: QLineEdit
    source: QLabel
    error: QLabel


# The widgets of each credential, by credential name.
type Fields = dict[str, CredentialField]


@dataclass(frozen=True)
class AccountControls:
    """The section's notes, buttons and results."""

    keyring_label: QLabel
    load_label: QLabel
    message_label: QLabel
    test_button: QPushButton
    save_button: QPushButton
    checks_label: QLabel


def _field(parent: QWidget) -> CredentialField:
    edit = QLineEdit(parent)
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    return CredentialField(edit, wrapped_label("", parent), error_line(parent))


def _group(title: str, names: tuple[str, ...], fields: Fields) -> QGroupBox:
    group = QGroupBox(title)
    rows = QFormLayout(group)
    for name in names:
        field = fields[name]
        rows.addRow(credential_label(name), field.edit)
        rows.addRow("", field.source)
        rows.addRow("", field.error)
    return group


def _controls(parent: QWidget) -> AccountControls:
    return AccountControls(
        keyring_label=wrapped_label("", parent),
        load_label=wrapped_label("", parent),
        message_label=wrapped_label("", parent),
        test_button=QPushButton(tr("Test"), parent),
        save_button=QPushButton(tr("Save"), parent),
        checks_label=wrapped_label("", parent),
    )


def _buttons(controls: AccountControls) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(controls.message_label, 1)
    row.addWidget(controls.test_button)
    row.addWidget(controls.save_button)
    return row


def _message_text(shown: AccountsState) -> str:
    """Return the message line; a problem of no field is said here, since no field is marked."""
    form_problem = shown.errors.get(FORM_FIELD, "")
    return f"{shown.message} {form_problem}".strip()


def _check_line(check: CheckRow) -> str:
    return f"{check.service}: {check.outcome_label}. {check.text}"


def _checks_text(shown: AccountsState) -> str:
    if shown.checking:
        return tr("Testing the accounts...")
    return "\n".join(map(_check_line, shown.checks))


def _paint_field(field: CredentialField, row: AccountRow, *, saving: bool) -> None:
    if not row.typed and field.edit.text():
        # Stored: what was typed is not kept in the field.
        field.edit.clear()
    # Nothing is typed while a store is in flight.
    field.edit.setEnabled(not saving)
    field.edit.setPlaceholderText(row.placeholder)
    show_text(field.source, row.source)
    show_text(field.error, row.error)


class AccountsView(QWidget):
    """The credentials, bound to an `AccountsViewModel`; also the wizard's first step."""

    def __init__(self, model: AccountsViewModel, parent: QWidget | None = None) -> None:
        """Build the fields and follow `model`."""
        super().__init__(parent)
        self._model = model
        self.fields: Fields = {name: _field(self) for name in CREDENTIAL_ORDER}
        self.controls = _controls(self)
        self._lay_out()
        for name, field in self.fields.items():
            field.edit.textEdited.connect(partial(model.type_value, name))
        self.controls.save_button.clicked.connect(self._save)
        self.controls.test_button.clicked.connect(model.test)
        model.subscribe(self._repaint)
        self._repaint()

    def _lay_out(self) -> None:
        controls = self.controls
        layout = QVBoxLayout(self)
        layout.addWidget(wrapped_label(runpod_optional_note(), self))
        layout.addWidget(controls.keyring_label)
        layout.addWidget(controls.load_label)
        layout.addWidget(_group(tr("R2 storage (required)"), R2_CREDENTIALS, self.fields))
        layout.addWidget(_group(tr("Runpod (optional)"), (OPTIONAL_CREDENTIAL,), self.fields))
        layout.addLayout(_buttons(controls))
        layout.addWidget(controls.checks_label)
        layout.addStretch()

    def _save(self) -> None:
        # Not connected to `save` itself: `clicked` would pass its flag as `on_stored`.
        self._model.save()

    def _repaint(self) -> None:
        shown = self._model.shown
        controls = self.controls
        keyring = "" if shown.status is None else keyring_note(shown.status.keyring)
        show_text(controls.keyring_label, keyring)
        show_text(controls.load_label, shown.load_error)
        for row in shown.rows():
            _paint_field(self.fields[row.name], row, saving=shown.saving)
        controls.message_label.setText(_message_text(shown))
        controls.save_button.setEnabled(shown.can_save)
        controls.test_button.setEnabled(not shown.checking)
        show_text(controls.checks_label, _checks_text(shown))
