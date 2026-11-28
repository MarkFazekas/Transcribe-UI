"""Flow tests of `transcribe-ui credentials` with a scripted prompt (plan m6, M6.7).

The session must answer exactly like `run` with the same `store_credentials` request file and
store the same values; it skips empty answers, refuses a stdin that is not a terminal, and never
shows a credential (stdout, stderr, prompts or log records).
"""

import getpass
import io
import json
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import pytest

from support.credentials import (
    NEVER_SHOWN,
    TEST_ACCESS_KEY_ID,
    TEST_BUCKET,
    TEST_ENDPOINT,
    TEST_RUNPOD_KEY,
    TEST_SECRET,
)
from support.fakes import seeded_ports
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.ports import Ports
from transcribe_ui.cli.console import Console
from transcribe_ui.cli.constants import EXIT_CANCELLED, EXIT_INVALID_REQUEST, EXIT_OK
from transcribe_ui.cli.credentials_command import answer_accepted
from transcribe_ui.cli.main import main
from transcribe_ui.cli.prompt import TerminalPrompt
from transcribe_ui.credentials.classes import CredentialName
from transcribe_ui.credentials.constants import (
    BUCKET_RULE,
    CREDENTIAL_ORDER,
    ENDPOINT_RULE,
    R2_ACCESS_KEY_ID,
    R2_BUCKET,
    R2_ENDPOINT_URL,
    VALUE_NOT_COMPACT,
)
from transcribe_ui.i18n.catalog import activate
from transcribe_ui.i18n.credential_texts import credential_labels, credential_rules

pytestmark = pytest.mark.usefixtures("no_credential_logs")

STATUS = '{"action": "credentials_status"}'
# The command line of the command under test.
CREDENTIALS = ("credentials",)
# Every credential, in the order `credentials` asks for them.
EVERY_ANSWER = (TEST_ENDPOINT, TEST_BUCKET, TEST_ACCESS_KEY_ID, TEST_SECRET, TEST_RUNPOD_KEY)
# Scripted sessions: every value; only some (Enter, or blanks, skips one); none.
SESSIONS = (
    EVERY_ANSWER,
    ("", "", TEST_ACCESS_KEY_ID, TEST_SECRET, "  "),
    ("", "", "", "", TEST_RUNPOD_KEY),
    ("", "", "", "", ""),
)
# Answers the request refuses: a URL that is not https, a key with a space.
BAD_ENDPOINT = "http://r2.example.invalid"
BAD_KEY = "AKID WITH SPACE"
# The longest bucket name R2 accepts.
BUCKET_MAX_LENGTH = 63
LONGEST_BUCKET = "a" * BUCKET_MAX_LENGTH
TOO_LONG_BUCKET = "a" * (BUCKET_MAX_LENGTH + 1)


@dataclass
class ScriptedPrompt:
    """A prompt that answers from a script and records what it was asked and told."""

    answers: list[str | BaseException]
    terminal: bool = True
    labels: list[str] = field(default_factory=list)
    # The labels asked with echo.
    echoed: list[str] = field(default_factory=list)
    told: list[str] = field(default_factory=list)

    def interactive(self) -> bool:
        """Return whether stdin counts as a terminal."""
        return self.terminal

    def tell(self, text: str) -> None:
        """Record the guidance."""
        self.told.append(text)

    def ask(self, label: str) -> str:
        """Record the label as echoed and return (or raise) the next scripted answer."""
        self.echoed.append(label)
        return self.ask_hidden(label)

    def ask_hidden(self, label: str) -> str:
        """Record the label and return (or raise) the next scripted answer.

        Raises:
            BaseException: The scripted exception (Ctrl+C, end of input).
        """
        self.labels.append(label)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


@dataclass
class RecordingGetpass:
    """Stands in for `getpass.getpass`: records the prompts and returns the answer."""

    answer: str
    prompts: list[str] = field(default_factory=list)

    def __call__(self, prompt: str = "Password: ", stream: object = None) -> str:
        """Record the prompt (the terminal is used, so no stream is passed)."""
        assert stream is None
        self.prompts.append(prompt)
        return self.answer


@dataclass(frozen=True)
class Outcome:
    """What a CLI call printed and returned."""

    code: int
    stdout: bytes
    stderr: bytes

    def text(self) -> str:
        """Return everything printed, as text."""
        return (self.stdout + self.stderr).decode("utf-8")


@pytest.fixture
def ports(tmp_path: Path) -> Iterator[Ports]:
    """Return seeded ports with an empty in-memory keyring (closed afterwards)."""
    seeded = seeded_ports(tmp_path / "prompted")
    with closing(seeded.state):
        yield seeded


def _cli(ports: Ports, argv: Sequence[str], prompt: ScriptedPrompt | None = None) -> Outcome:
    stdout = io.BytesIO()
    stderr = io.BytesIO()
    console = Console(stdin=io.BytesIO(), stdout=stdout, stderr=stderr, prompt=prompt)
    code = main(argv, make_backend=lambda: Backend(ports), console=console)
    return Outcome(code, stdout.getvalue(), stderr.getvalue())


# Returns the `credentials_status` response of the test's ports.
type Status = Callable[[], bytes]


def _sources(status_json: bytes) -> set[str]:
    return {entry["source"] for entry in json.loads(status_json)["credentials"]}


@pytest.fixture
def status(ports: Ports, tmp_path: Path) -> Status:
    """Return a function giving the `credentials_status` response of `ports`."""
    return partial(_status, ports, tmp_path)


def _status(ports: Ports, tmp_path: Path) -> bytes:
    request_file = tmp_path / "status.json"
    request_file.write_text(STATUS, encoding="utf-8")
    return _cli(ports, ["run", str(request_file)]).stdout


def _assert_nothing_shown(outcome: Outcome, prompt: ScriptedPrompt, *typed: str) -> None:
    shown = [outcome.text(), *prompt.labels, *prompt.told]
    for secret in (*NEVER_SHOWN, *typed):
        assert all(secret not in text for text in shown)


@pytest.mark.parametrize("answers", SESSIONS)
def test_session_equals_the_request_file(
    tmp_path: Path, ports: Ports, status: Status, answers: tuple[str, ...]
) -> None:
    """A session answers and stores exactly like `run` with the request of its non-empty answers."""
    given = {name: answer for name, answer in zip(CREDENTIAL_ORDER, answers, strict=True) if answer.strip()}
    request_file = tmp_path / "store.json"
    request_file.write_text(json.dumps({"action": "store_credentials", **given}), encoding="utf-8")
    from_file_ports = seeded_ports(tmp_path / "from-file")
    with closing(from_file_ports.state):
        expected = _cli(from_file_ports, ["run", str(request_file)])
        expected_status = _status(from_file_ports, tmp_path)
    prompt = ScriptedPrompt(list(answers))
    prompted = _cli(ports, CREDENTIALS, prompt)
    assert (prompted.code, prompted.stdout) == (expected.code, expected.stdout)
    assert status() == expected_status
    labels = credential_labels()
    assert prompt.labels == list(labels.values())
    assert prompt.echoed == [labels[R2_ENDPOINT_URL], labels[R2_BUCKET]]
    assert not prompted.stderr
    _assert_nothing_shown(prompted, prompt)


def test_refused_answer_is_asked_again(ports: Ports, status: Status) -> None:
    """An answer the request refuses shows the field's rule and asks again; the answers before it stay."""
    prompt = ScriptedPrompt([BAD_ENDPOINT, *EVERY_ANSWER[:2], BAD_KEY, *EVERY_ANSWER[2:]])
    outcome = _cli(ports, CREDENTIALS, prompt)
    labels = list(credential_labels().values())
    rules = credential_rules()
    assert prompt.labels == [labels[0], *labels[:3], *labels[2:]]
    assert prompt.told[-2:] == [rules[R2_ENDPOINT_URL], rules[R2_ACCESS_KEY_ID]]
    assert (outcome.code, _sources(status())) == (EXIT_OK, {"keyring"})
    _assert_nothing_shown(outcome, prompt, BAD_KEY)


def test_session_stores_every_credential(ports: Ports, status: Status) -> None:
    """A full session exits 0 and every credential then comes from the keyring."""
    prompt = ScriptedPrompt(list(EVERY_ANSWER))
    assert _cli(ports, CREDENTIALS, prompt).code == EXIT_OK
    assert _sources(status()) == {"keyring"}
    assert prompt.told


def test_stdin_not_a_terminal_is_refused(ports: Ports, status: Status) -> None:
    """Without a terminal nothing is asked or stored, and the answer is `invalid_arguments` (exit 2)."""
    before = status()
    prompt = ScriptedPrompt(list(EVERY_ANSWER), terminal=False)
    outcome = _cli(ports, CREDENTIALS, prompt)
    assert outcome.code == EXIT_INVALID_REQUEST
    assert json.loads(outcome.stdout)["error"]["code"] == "invalid_arguments"
    assert not prompt.labels
    assert not prompt.told
    assert status() == before


@pytest.mark.parametrize("stop", [KeyboardInterrupt(), EOFError()])
def test_stopping_while_asked_stores_nothing(ports: Ports, status: Status, stop: BaseException) -> None:
    """Ctrl+C or the end of input during the prompts answers `cancelled` (exit 130) and stores nothing."""
    before = status()
    prompt = ScriptedPrompt([TEST_ENDPOINT, TEST_BUCKET, stop])
    outcome = _cli(ports, CREDENTIALS, prompt)
    assert outcome.code == EXIT_CANCELLED
    assert json.loads(outcome.stdout)["error"]["code"] == "cancelled"
    assert status() == before
    _assert_nothing_shown(outcome, prompt)


def test_prompts_are_in_the_active_language() -> None:
    """The labels come from the catalog."""
    activate("hu")
    assert credential_labels()["runpod_api_key"] == "Runpod API-kulcs"


def test_terminal_prompt_hides_only_the_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hidden answers go through `getpass`; echoed ones are read from stdin; labels and guidance go to stderr."""
    fake = RecordingGetpass(TEST_SECRET)
    monkeypatch.setattr(getpass, "getpass", fake)
    stderr = io.BytesIO()
    prompt = TerminalPrompt(stderr, io.StringIO(f"{TEST_BUCKET}\r\n"), stdin_is_terminal=True)
    prompt.tell("Hozzáférési adatok")
    assert prompt.ask("R2 bucket name") == TEST_BUCKET
    assert prompt.ask_hidden("R2 secret access key") == TEST_SECRET
    assert fake.prompts == ["R2 secret access key: "]
    assert stderr.getvalue() == "Hozzáférési adatok\nR2 bucket name: ".encode()


@pytest.mark.parametrize("typed", ["\x1a", "\x04"])
def test_windows_end_of_input_cancels(
    ports: Ports, status: Status, monkeypatch: pytest.MonkeyPatch, typed: str
) -> None:
    """Ctrl+Z or Ctrl+D, which Windows `getpass` returns as a character, cancels like the end of input."""
    before = status()
    monkeypatch.setattr(getpass, "getpass", RecordingGetpass(typed))
    stdout = io.BytesIO()
    stderr = io.BytesIO()
    lines = io.StringIO(f"{TEST_ENDPOINT}\n{TEST_BUCKET}\n")
    prompt = TerminalPrompt(stderr, lines, stdin_is_terminal=True)
    console = Console(stdin=io.BytesIO(), stdout=stdout, stderr=stderr, prompt=prompt)
    assert main(CREDENTIALS, make_backend=lambda: Backend(ports), console=console) == EXIT_CANCELLED
    assert json.loads(stdout.getvalue())["error"]["code"] == "cancelled"
    assert status() == before
    with pytest.raises(EOFError):
        TerminalPrompt(io.BytesIO(), io.StringIO(f"{typed}\n"), stdin_is_terminal=True).ask("R2 bucket name")


def test_echoed_answer_at_end_of_input_cancels() -> None:
    """A stdin that ends before a line raises EOFError (cancelled by the command)."""
    with pytest.raises(EOFError):
        TerminalPrompt(io.BytesIO(), io.StringIO(), stdin_is_terminal=True).ask("R2 bucket name")


def test_terminal_prompt_follows_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """The process's prompt is interactive only when stdin is a terminal."""
    monkeypatch.setattr(sys, "stdin", io.StringIO("piped"))
    assert not TerminalPrompt.on_process(io.BytesIO()).interactive()
    monkeypatch.setattr(sys, "stdin", None)
    assert not TerminalPrompt.on_process(io.BytesIO()).interactive()


def test_rules_quote_the_backend() -> None:
    """The English rule texts carry the wording of the backend's validation messages."""
    rules = credential_rules()
    assert ENDPOINT_RULE in rules[R2_ENDPOINT_URL]
    assert BUCKET_RULE in rules[R2_BUCKET]
    assert VALUE_NOT_COMPACT in rules[R2_ACCESS_KEY_ID]


@pytest.mark.parametrize(
    ("name", "answer", "accepted"),
    [
        (R2_BUCKET, "abc", True),
        (R2_BUCKET, LONGEST_BUCKET, True),
        (R2_BUCKET, "ab", False),
        (R2_BUCKET, TOO_LONG_BUCKET, False),
        (R2_ENDPOINT_URL, TEST_ENDPOINT, True),
        (R2_ENDPOINT_URL, BAD_ENDPOINT, False),
        (R2_ACCESS_KEY_ID, BAD_KEY, False),
    ],
)
def test_answers_are_checked_by_the_backend_rules(name: CredentialName, answer: str, *, accepted: bool) -> None:
    """The bucket's length limits and the endpoint's scheme are checked where the user types."""
    assert answer_accepted(name, answer) is accepted


def test_answer_not_in_the_encoding_is_refused() -> None:
    """A line that is not text in the terminal's encoding is refused (asked again), not a crash."""
    lines = io.TextIOWrapper(io.BytesIO(b"\xff\xfe\n"), encoding="utf-8")
    assert not answer_accepted(R2_BUCKET, TerminalPrompt(io.BytesIO(), lines, stdin_is_terminal=True).ask("x"))
