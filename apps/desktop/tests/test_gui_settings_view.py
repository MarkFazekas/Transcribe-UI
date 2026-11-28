"""Smoke tests of the settings area's widgets: the wizard, the pages, the notice and the restart (plan m6 M6.3)."""

from collections.abc import Iterator
from contextlib import ExitStack, closing
from functools import partial
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QLineEdit
from pytestqt.qtbot import QtBot

from support.credentials import (
    NEVER_SHOWN,
    TEST_ACCESS_KEY_ID,
    TEST_BUCKET,
    TEST_ENDPOINT,
    TEST_RUNPOD_KEY,
    TEST_SECRET,
    store_request,
)
from support.fakes import seeded_ports
from support.gated import UPLOAD, GatedRunner, upload, wait_for
from support.golden import GOLDEN_DIR
from support.gui import window_over
from support.gui_parity import run_gui_one
from support.gui_settings import Deferred, Recorded
from transcribe_ui.api.backend import Backend
from transcribe_ui.cli.constants import EXIT_OK
from transcribe_ui.credentials.classes import CredentialsStatusResponse
from transcribe_ui.gui.activity import Activity
from transcribe_ui.gui.app import application, run_gui
from transcribe_ui.gui.constants import PAGE_SETTINGS
from transcribe_ui.gui.language import StartLanguages, use_language
from transcribe_ui.gui.settings.accounts import AccountsViewModel
from transcribe_ui.gui.settings.accounts_view import AccountsView
from transcribe_ui.gui.settings.cloud_setup import CloudSetupViewModel
from transcribe_ui.gui.settings.hooks import AreaHooks
from transcribe_ui.gui.settings.install import install_settings
from transcribe_ui.gui.settings.notice_view import NoticeBar, RestartQuestion
from transcribe_ui.gui.settings.texts import settings_hint
from transcribe_ui.gui.settings.view import SettingsPage
from transcribe_ui.gui.settings.wizard import SetupWizard
from transcribe_ui.gui.shell import MainWindow

# The R2 values typed into the wizard, by credential name.
R2_TYPED = (
    ("r2_endpoint_url", TEST_ENDPOINT),
    ("r2_bucket", TEST_BUCKET),
    ("r2_access_key_id", TEST_ACCESS_KEY_ID),
    ("r2_secret_access_key", TEST_SECRET),
)
VIDEO = "video_extensions"


@pytest.fixture
def backend(tmp_path: Path) -> Iterator[Backend]:
    """Return a seeded backend with an empty keyring (closed afterwards)."""
    with closing(Backend(seeded_ports(tmp_path / "leg"))) as created:
        yield created


def _started(qtbot: QtBot, backend: Backend, log_dir: Path) -> MainWindow:
    """Return the window over `backend` after its start-up steps ran."""
    window = window_over(qtbot, backend, log_dir)
    finished: list[str] = []
    window.context.hooks.run(partial(finished.append, "done"))
    wait_for(qtbot, lambda: bool(finished))
    return window


def _settings(window: MainWindow) -> SettingsPage:
    page = window.pages.page(PAGE_SETTINGS)
    assert isinstance(page, SettingsPage)
    return page


def accounts_status(window: MainWindow) -> CredentialsStatusResponse | None:
    """Return the accounts' last read status."""
    return _settings(window).models.accounts.shown.status


def _wizard(window: MainWindow) -> SetupWizard:
    wizard = window.findChild(SetupWizard)
    assert wizard is not None
    return wizard


def _notice(window: MainWindow) -> NoticeBar:
    notice = window.findChild(NoticeBar)
    assert notice is not None
    return notice


def _drained(qtbot: QtBot, window: MainWindow) -> None:
    wait_for(qtbot, lambda: not window.busy())


def _texts(window: MainWindow, wizard: SetupWizard) -> str:
    labels = [*window.findChildren(QLabel), *wizard.findChildren(QLabel)]
    return "\n".join(label.text() for label in labels)


def _type_r2(view: AccountsView) -> list[QLineEdit]:
    edits = [view.fields[name].edit for name, _typed in R2_TYPED]
    for edit, (_name, typed) in zip(edits, R2_TYPED, strict=True):
        QTest.keyClicks(edit, typed)
    return edits


@pytest.mark.usefixtures("no_credential_logs")
def test_the_wizard_stores_the_accounts(qtbot: QtBot, tmp_path: Path, backend: Backend) -> None:
    """Missing R2 values open the wizard at accounts; typed values are stored, never shown, and the notice goes."""
    window = _started(qtbot, backend, tmp_path)
    wizard = _wizard(window)
    assert (wizard.isVisible(), _notice(window).isVisible()) == (True, True)
    view = wizard.pages.widget(0)
    assert isinstance(view, AccountsView)
    edits = _type_r2(view)
    view.controls.save_button.click()
    accounts = _settings(window).models.accounts
    wait_for(qtbot, lambda: accounts.shown.message == "Saved." and not window.busy())
    fields = {(edit.text(), edit.echoMode()) for edit in edits}
    assert (fields, _notice(window).isVisible()) == ({("", QLineEdit.EchoMode.Password)}, False)
    shown = _texts(window, wizard)
    assert ("Stored in the keyring" in shown, [secret for secret in NEVER_SHOWN if secret in shown]) == (True, [])
    # No Runpod key: accounts is the last step.
    finish = wizard.next_button.text()
    wizard.next_button.click()
    assert (finish, wizard.isVisible(), window.windowTitle().startswith("Transcribe-UI ")) == ("Finish", False, True)


@pytest.mark.usefixtures("no_credential_logs")
def test_finish_stores_what_was_typed(qtbot: QtBot, tmp_path: Path, backend: Backend) -> None:
    """Finish stores the typed values first and closes once they are stored."""
    window = _started(qtbot, backend, tmp_path)
    wizard = _wizard(window)
    view = wizard.pages.widget(0)
    assert isinstance(view, AccountsView)
    _type_r2(view)
    wizard.next_button.click()
    wait_for(qtbot, lambda: not wizard.isVisible())
    status = accounts_status(window)
    assert status is not None
    assert {credential.source for credential in status.credentials if credential.name != "runpod_api_key"} == {
        "keyring",
    }
    _drained(qtbot, window)


def test_a_refused_value_keeps_the_wizard_open(qtbot: QtBot, tmp_path: Path, backend: Backend) -> None:
    """A refused value keeps the wizard on accounts with its hint; closing it forgets what was typed."""
    window = _started(qtbot, backend, tmp_path)
    wizard = _wizard(window)
    view = wizard.pages.widget(0)
    assert isinstance(view, AccountsView)
    QTest.keyClicks(view.fields["r2_bucket"].edit, "Not_A_Bucket")
    wizard.next_button.click()
    assert (wizard.isVisible(), view.fields["r2_bucket"].error.isVisibleTo(view)) == (True, True)
    wizard.reject()
    accounts = _settings(window).accounts
    fields = {field.edit.text() for field in accounts.fields.values()}
    assert (fields, accounts.controls.save_button.isEnabled()) == ({""}, False)
    _drained(qtbot, window)


def test_the_preferences_show_field_errors(qtbot: QtBot, tmp_path: Path, backend: Backend) -> None:
    """A refused field shows how to fix it and keeps the text; fixing the text hides the error."""
    window = _started(qtbot, backend, tmp_path)
    window.context.signals.open_settings.emit("")
    view = _settings(window).preferences
    video = view.fields.video
    video.selectAll()
    QTest.keyClicks(video, ".MKV mp4")
    view.controls.save_button.click()
    error = view.errors[VIDEO]
    assert (window.pages.current_page(), video.text()) == (PAGE_SETTINGS, ".MKV mp4")
    assert (error.isVisibleTo(view), error.text()) == (True, settings_hint(VIDEO))
    video.selectAll()
    QTest.keyClicks(video, "mkv mp4")
    assert not error.isVisibleTo(view)
    _drained(qtbot, window)


def test_the_preferences_save(qtbot: QtBot, tmp_path: Path, backend: Backend) -> None:
    """A ticked language and an edited field are saved; the window hears `settings_changed`."""
    window = _started(qtbot, backend, tmp_path)
    page = _settings(window)
    view = page.preferences
    model = page.models.preferences
    table = view.fields.languages
    german = table.table.item(table.row_of("de"), 0)
    assert german is not None
    german.setCheckState(Qt.CheckState.Checked)
    view.fields.hidden.setChecked(True)
    with qtbot.waitSignal(window.context.signals.settings_changed):
        view.controls.save_button.click()
    saved = model.shown.saved
    assert saved is not None
    assert (model.shown.message, saved.enabled_languages, saved.include_hidden) == ("Saved.", ("hu", "en", "de"), True)
    _drained(qtbot, window)


def test_a_language_change_asks_to_restart(qtbot: QtBot, tmp_path: Path, backend: Backend) -> None:
    """Saving another language asks; Restart now requests the restart and closes the window."""
    window = _started(qtbot, backend, tmp_path)
    _wizard(window).reject()
    view = _settings(window).preferences
    language = view.fields.language
    language.setCurrentIndex(language.findData("hu"))
    view.controls.save_button.click()
    wait_for(qtbot, lambda: window.findChild(RestartQuestion) is not None)
    question = window.findChild(RestartQuestion)
    assert question is not None
    _drained(qtbot, window)
    question.restart_button.click()
    assert (window.context.restart.requested, window.isVisible()) == (True, False)


def test_keeping_the_window_open_drops_the_restart(qtbot: QtBot, tmp_path: Path) -> None:
    """With work in flight the close asks first (Q30); keeping the window open drops the restart."""
    gated = GatedRunner((UPLOAD,))
    window = window_over(qtbot, gated, tmp_path)
    window.context.executor.submit(upload(), lambda _response: None)
    wait_for(qtbot, lambda: gated.started(UPLOAD) == 1)
    window.context.restart.request()
    window.close()
    question = window.closing.question
    assert question is not None
    question.reject()
    assert (window.context.restart.requested, window.isVisible()) == (False, True)
    gated.release()
    _drained(qtbot, window)


def test_the_cloud_setup_page_sets_up(qtbot: QtBot, tmp_path: Path, backend: Backend) -> None:
    """With every credential, the wizard opens at the cloud setup; Set up on the page makes it ready."""
    assert backend.execute_json(store_request()).status == "ok"
    window = _started(qtbot, backend, tmp_path)
    wizard = _wizard(window)
    assert wizard.pages.currentIndex() == 1
    wizard.reject()
    window.context.signals.open_settings.emit("cloud_setup")
    page = _settings(window)
    controls = page.cloud_setup.controls
    assert (page.currentWidget(), controls.table.rowCount(), controls.setup_button.text()) == (
        page.cloud_setup,
        len(page.models.cloud.rows()),
        "Set up",
    )
    controls.setup_button.click()
    wait_for(qtbot, lambda: controls.state_label.text().startswith("Cloud setup done"))
    _drained(qtbot, window)
    assert (controls.setup_button.text(), _notice(window).isVisible()) == ("Update", False)


def test_the_settings_request_on_the_gui_leg(qtbot: QtBot, tmp_path: Path) -> None:
    """The update golden runs through `QtExecutor` with the backend's response (the `gui` parity leg)."""
    document = (GOLDEN_DIR / "gui_settings_update.json").read_text(encoding="utf-8")
    assert run_gui_one(tmp_path, qtbot, document).exit_code == EXIT_OK


class _Closing(Backend):
    """A backend that records when it is closed."""

    def __init__(self, root: Path, events: list[str]) -> None:
        super().__init__(seeded_ports(root))
        self._events = events

    def close(self) -> None:
        super().close()
        self._events.append("closed")


def _close_when_idle(window: MainWindow) -> None:
    if not window.busy():
        window.close()


def _restart_when_idle(window: MainWindow) -> None:
    window.context.restart.request()
    timer = QTimer(window)
    timer.timeout.connect(partial(_close_when_idle, window))
    timer.start(1)


def test_run_gui_restarts_after_closing(qtbot: QtBot, tmp_path: Path) -> None:
    """A requested restart starts the GUI again once the window and the backend are closed."""
    assert qtbot is not None
    events: list[str] = []
    code = run_gui(
        partial(_Closing, tmp_path, events),
        on_window=_restart_when_idle,
        relaunch=partial(events.append, "relaunched"),
    )
    assert (code, events) == (0, ["closed", "relaunched"])


def _hungarian_os(reads: list[str]) -> str:
    reads.append("read")
    return "hu"


def test_the_os_language_is_read_once(qtbot: QtBot) -> None:
    """Start-up reads the OS language once and keeps what `system` shows (never read again later)."""
    assert qtbot is not None
    reads: list[str] = []
    with ExitStack() as stack:
        languages = use_language("en", application(), stack, partial(_hungarian_os, reads))
    assert (languages, reads) == (StartLanguages(shown="en", system="hu"), ["read"])


def _wizard_over(qtbot: QtBot, deferred: Deferred) -> tuple[SetupWizard, AccountsViewModel]:
    """Return a shown wizard over view-models that send through `deferred` (the status already read)."""
    accounts = AccountsViewModel(deferred, AreaHooks())
    cloud = CloudSetupViewModel(deferred, Activity(deferred), accounts)
    accounts.load()
    deferred.answer()
    wizard = SetupWizard(accounts, cloud, lambda _view: None)
    qtbot.addWidget(wizard)
    wizard.show()
    view = wizard.pages.widget(0)
    assert isinstance(view, AccountsView)
    _type_r2(view)
    QTest.keyClicks(view.fields["runpod_api_key"].edit, TEST_RUNPOD_KEY)
    return wizard, accounts


@pytest.mark.usefixtures("no_credential_logs")
def test_next_moves_on_once_per_store(qtbot: QtBot, backend: Backend) -> None:
    """Next is blocked while the store is in flight; the wizard moves on once, then checks the cloud once."""
    deferred = Deferred(backend)
    wizard, _accounts = _wizard_over(qtbot, deferred)
    wizard.next_button.click()
    blocked = (wizard.next_button.isEnabled(), wizard.back_button.isEnabled())
    wizard.next_button.click()
    assert (blocked, deferred.actions()) == ((False, False), ["store_credentials"])
    assert (deferred.answer(), deferred.answer()) == ("store_credentials", "credentials_status")
    assert (wizard.pages.currentIndex(), wizard.isVisible(), deferred.actions()) == (1, True, ["cloud_setup_status"])


@pytest.mark.usefixtures("no_credential_logs")
def test_a_store_after_set_up_later_changes_nothing(qtbot: QtBot, backend: Backend) -> None:
    """A store answering after "Set up later" neither moves the closed wizard nor forgets newer typing."""
    deferred = Deferred(backend)
    wizard, accounts = _wizard_over(qtbot, deferred)
    wizard.next_button.click()
    wizard.reject()
    accounts.type_value("r2_bucket", "typed-in-settings")
    deferred.answer()
    deferred.answer()
    assert (wizard.isVisible(), deferred.actions(), accounts.shown.typed) == (False, [], frozenset(("r2_bucket",)))


def test_a_form_problem_shows_in_the_message_line(qtbot: QtBot, backend: Backend) -> None:
    """A problem of no field is said in the accounts' message line, next to "Fix the marked fields"."""
    accounts = AccountsViewModel(Recorded(backend), AreaHooks())
    view = AccountsView(accounts)
    qtbot.addWidget(view)
    accounts.shown.errors = {"": "This value is not valid."}
    accounts.shown.message = "Fix the marked fields, then save again."
    accounts.changed()
    shown = view.controls.message_label.text()
    assert shown == "Fix the marked fields, then save again. This value is not valid."


def test_the_wizard_is_opened_again_not_rebuilt(qtbot: QtBot, tmp_path: Path, backend: Backend) -> None:
    """Opening the wizard a second time reuses it: one wizard, one set of views following the view-models."""
    window = window_over(qtbot, backend, tmp_path)
    area = install_settings(window)
    area.open_wizard("accounts")
    first = area.wizard
    assert first is not None
    first.reject()
    area.open_wizard("accounts")
    # The window's own area opened none (its start-up steps did not run): one wizard in all.
    reused = (area.wizard is first, first.isVisible(), len(window.findChildren(SetupWizard)))
    assert reused == (True, True, 1)
