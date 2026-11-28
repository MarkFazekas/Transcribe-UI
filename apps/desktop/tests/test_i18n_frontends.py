"""The frontends in Hungarian and English: help texts, the GUI, and language-neutral responses (plan m6 M6.14).

Only the frontends translate: the backend's responses, and the CLI's JSON, are byte-identical
whatever the language.
"""

import io
import sys
from collections.abc import Iterator
from contextlib import closing
from functools import partial
from pathlib import Path
from typing import Final

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel
from pytestqt.qtbot import QtBot

from support.fakes import seeded_ports
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import ErrorBody, ErrorResponse, LanguageSetting
from transcribe_ui.api.ports import Ports
from transcribe_ui.api.schema import response_adapter
from transcribe_ui.cli.main import main
from transcribe_ui.gui.app import run_gui
from transcribe_ui.gui.dialogs import ErrorDialog
from transcribe_ui.gui.feedback import error_view
from transcribe_ui.gui.shell import MainWindow
from transcribe_ui.i18n.catalog import activate
from transcribe_ui.state.settings import default_settings

BATCH_ID = "batch_20261127_120000"
ENGLISH: Final = "en"
HUNGARIAN: Final = "hu"
# Requests whose responses carry coded texts: an error, a validation error, failed checks, settings.
REQUESTS = (
    '{"action": "get_batch", "batch_id": "batch_20261127_120000"}',
    '{"action": "get_batch"}',
    '{"action": "check_credentials"}',
    '{"action": "get_settings"}',
)


def _ports(root: Path, language: LanguageSetting) -> Ports:
    ports = seeded_ports(root)
    ports.state.settings.save(default_settings().model_copy(update={"language": language}))
    return ports


@pytest.fixture
def hungarian(tmp_path: Path) -> Iterator[Ports]:
    """Return seeded ports whose settings choose Hungarian (closed afterwards)."""
    ports = _ports(tmp_path / HUNGARIAN, HUNGARIAN)
    with closing(ports.state):
        yield ports


def _help(ports: Ports, argv: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    with pytest.raises(SystemExit) as exited:
        main(argv, make_backend=partial(Backend, ports), stdout=io.BytesIO())
    assert exited.value.code == 0
    # argparse wraps the help to the terminal's width.
    return " ".join(capsys.readouterr().out.split())


def test_help_shows_the_apps_texts_in_hungarian(hungarian: Ports, capsys: pytest.CaptureFixture[str]) -> None:
    """With the setting `hu`, `--help` and `run --help` show the app's help texts in Hungarian."""
    shown = _help(hungarian, ["--help"], capsys)
    assert "Transcribe-UI-kérések futtatása JSON-fájlokból." in shown
    assert "az alkalmazás verziójának kiírása, majd kilépés" in shown
    assert "egy JSON-fájlban lévő kérés futtatása" in shown
    assert "a kérések JSON-sémájának kiírása" in shown
    assert "a kérést tartalmazó JSON-fájl útvonala" in _help(hungarian, ["run", "--help"], capsys)


def test_help_is_utf8_on_a_narrow_console(hungarian: Ports, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--help` to a cp1252 stdout (a Windows pipe) is written as UTF-8 instead of failing on `ő`."""
    written = io.BytesIO()
    console = io.TextIOWrapper(written, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", console)
    with pytest.raises(SystemExit) as exited:
        main(["--help"], make_backend=partial(Backend, hungarian), stdout=io.BytesIO())
    console.flush()
    assert (exited.value.code, console.errors) == (0, "strict")
    shown = " ".join(written.getvalue().decode("utf-8").split())
    assert "egy JSON-fájlban lévő kérés futtatása" in shown


def test_help_follows_the_os_language(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """With `system` (the default), English unless the OS language is Hungarian (here: none)."""
    ports = seeded_ports(tmp_path)
    with closing(ports.state):
        assert "Run Transcribe-UI requests from JSON files." in _help(ports, ["--help"], capsys)


def _cli_output(ports: Ports, request_file: Path) -> bytes:
    stdout = io.BytesIO()
    main(["run", str(request_file)], make_backend=partial(Backend, ports), stdout=stdout, stderr=io.BytesIO())
    return stdout.getvalue()


@pytest.mark.parametrize("request_json", REQUESTS)
def test_cli_json_is_the_same_in_both_languages(tmp_path: Path, request_json: str) -> None:
    """The CLI's JSON (a machine contract) does not depend on the language setting."""
    request_file = tmp_path / "request.json"
    request_file.write_text(request_json, encoding="utf-8")
    outputs = []
    for language in (ENGLISH, HUNGARIAN):
        ports = _ports(tmp_path / language, language)
        with closing(ports.state):
            outputs.append(_cli_output(ports, request_file))
    english, hungarian = outputs
    assert english == hungarian.replace(b'"language": "hu"', b'"language": "en"')


@pytest.mark.parametrize("argv", [["run"], ["foo"], ["run", "a.json", "extra"]])
def test_cli_usage_errors_are_language_neutral(tmp_path: Path, argv: list[str]) -> None:
    """A command line argparse refuses gives the same `invalid_arguments` JSON in both languages."""
    outputs = []
    for language in (ENGLISH, HUNGARIAN):
        ports = _ports(tmp_path / language, language)
        stdout = io.BytesIO()
        with closing(ports.state):
            main(argv, make_backend=partial(Backend, ports), stdout=stdout)
        outputs.append(stdout.getvalue())
    assert outputs[0] == outputs[1]
    assert b"invalid_arguments" in outputs[0]


@pytest.mark.parametrize("request_json", REQUESTS)
def test_backend_bytes_are_the_same_in_both_languages(tmp_path: Path, request_json: str) -> None:
    """The same request gives the same response bytes whichever language the frontend shows."""
    ports = seeded_ports(tmp_path)
    with closing(ports.state):
        backend = Backend(ports)
        rendered = []
        for language in (ENGLISH, HUNGARIAN):
            activate(language)
            response = backend.execute_json(request_json)
            rendered.append(response_adapter().dump_json(response))
    assert rendered[0] == rendered[1]


def test_the_gui_shows_a_hungarian_error(qtbot: QtBot, tmp_path: Path) -> None:
    """Under `hu` the error dialog shows the catalog text of the code, not the backend's English message."""
    activate(HUNGARIAN)
    body = ErrorBody(code="batch_not_found", message="There is no batch with this id.", details={"batch_id": BATCH_ID})
    view = error_view(ErrorResponse(action="get_batch", error=body))
    assert view.message == f"Nincs {BATCH_ID} azonosítójú feladat."
    dialog = ErrorDialog(view, tmp_path, lambda _fix: None)
    qtbot.addWidget(dialog)
    texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert view.message in texts
    assert dialog.windowTitle() == "Hiba történt"


def _record_and_quit(shown: list[tuple[str, ...]], window: MainWindow) -> None:
    navigation = window.pages.navigation
    titles = tuple(navigation.item(row).text() for row in range(navigation.count()))
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
    cancel = box.button(QDialogButtonBox.StandardButton.Cancel).text()
    # Some styles mark a mnemonic (`&Cancel`).
    shown.append((*titles, cancel.replace("&", "")))
    QTimer.singleShot(0, partial(QApplication.exit, 0))


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        (HUNGARIAN, ("Új feladat", "Feladatok", "Felhő", "Beállítások", "Mégse")),
        (ENGLISH, ("New job", "Jobs", "Cloud", "Settings", "Cancel")),
    ],
)
def test_the_gui_starts_in_the_set_language(
    qtbot: QtBot,
    tmp_path: Path,
    language: LanguageSetting,
    expected: tuple[str, ...],
) -> None:
    """`run_gui` resolves the setting first: the pages and Qt's own buttons are in that language."""
    assert qtbot is not None
    shown: list[tuple[str, ...]] = []
    code = run_gui(lambda: Backend(_ports(tmp_path, language)), on_window=partial(_record_and_quit, shown))
    assert (code, shown) == (0, [expected])
