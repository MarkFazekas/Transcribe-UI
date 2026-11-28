"""The accounts page: where each credential is set, storing new values, and the credential test (§3.6).

Qt-free. Values never leave the fields they are typed into except in the `store_credentials`
request: the view-model keeps what was typed as `SecretStr` until Save, its display data names
credentials and where they are set (never a value), and a refused save keeps what was typed.
Only the fields the user typed into are sent.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial

from pydantic import JsonValue, SecretStr

from transcribe_ui.api.classes import ErrorResponse, Request, Response
from transcribe_ui.credentials.classes import (
    CheckCredentialsRequest,
    CheckCredentialsResponse,
    CheckOutcome,
    CredentialName,
    CredentialsStatusRequest,
    CredentialsStatusResponse,
    CredentialStatus,
    ServiceCheck,
    StoreCredentialsResponse,
)
from transcribe_ui.gui.executor import Executor
from transcribe_ui.gui.feedback import error_message, error_view
from transcribe_ui.gui.settings import account_texts
from transcribe_ui.gui.settings.constants import CREDENTIALS_LOCATION
from transcribe_ui.gui.settings.hooks import AreaHooks
from transcribe_ui.gui.settings.problems import field_errors, validated
from transcribe_ui.gui.viewmodel import ViewModel
from transcribe_ui.i18n.catalog import tr
from transcribe_ui.i18n.texts import code_text

# Values typed into credential fields, by credential name.
type Typed = Mapping[str, SecretStr]
# Called once a start-up step's request answered.
type Proceed = Callable[[], None]


@dataclass(frozen=True)
class AccountRow:
    """One credential as the accounts page shows it: never its value."""

    name: CredentialName
    label: str
    # Where it is set: the keyring, an environment variable, or nowhere.
    source: str
    placeholder: str
    # How to fix the value the last save refused; "" when none.
    error: str
    # Whether the user typed a new value (not saved yet).
    typed: bool


@dataclass(frozen=True)
class CheckRow:
    """The result of trying one service's credentials."""

    service: str
    outcome: CheckOutcome
    outcome_label: str
    text: str


def _not_sent(typed: Typed, sent: Typed) -> dict[str, SecretStr]:
    """Return the typed values that differ from what was sent (typed while the store was in flight)."""
    return dict(filter(partial(_unsent, sent), typed.items()))


def _unsent(sent: Typed, typed: tuple[str, SecretStr]) -> bool:
    name, secret = typed
    return sent.get(name) != secret


def store_request(typed: Mapping[str, SecretStr]) -> Request | ErrorResponse:
    """Return the `store_credentials` request of the typed values, or the backend's `invalid_request` for it."""
    document: dict[str, JsonValue] = {"action": "store_credentials"}
    for name, secret in typed.items():
        document[name] = secret.get_secret_value()
    return validated(document)


@dataclass
class AccountsState:
    """What the accounts page shows (never a value)."""

    status: CredentialsStatusResponse | None = None
    load_error: str = ""
    # How to fix each value the last save refused, by credential name.
    errors: dict[str, str] = field(default_factory=dict)
    checks: tuple[CheckRow, ...] = ()
    message: str = ""
    saving: bool = False
    checking: bool = False
    # The credentials the user typed a new value for (names only; the values stay in the view-model).
    typed: frozenset[str] = frozenset()

    @property
    def can_save(self) -> bool:
        """Return whether Save applies: a value was typed and no save runs."""
        return bool(self.typed) and not self.saving

    def rows(self) -> tuple[AccountRow, ...]:
        """Return each credential's display data (empty until the status is loaded)."""
        if self.status is None:
            return ()
        return tuple(self._row(credential) for credential in self.status.credentials)

    def _row(self, credential: CredentialStatus) -> AccountRow:
        return AccountRow(
            name=credential.name,
            label=account_texts.credential_label(credential.name),
            source=account_texts.credential_source(credential),
            placeholder=account_texts.credential_placeholder(credential),
            error=self.errors.get(credential.name, ""),
            typed=credential.name in self.typed,
        )


def _check_row(check: ServiceCheck) -> CheckRow:
    text = code_text("check", check.code, check.details, check.message)
    return CheckRow(
        account_texts.service_label(check.service), check.outcome, account_texts.outcome_label(check.outcome), text
    )


class AccountsViewModel(ViewModel):
    """The credentials: their status, new values typed in, Save and Test."""

    def __init__(self, executor: Executor, hooks: AreaHooks) -> None:
        """Send through `executor`; show errors through `hooks`."""
        super().__init__(executor)
        self._hooks = hooks
        self.shown = AccountsState()
        self._typed: dict[str, SecretStr] = {}

    def load(self, proceed: Proceed | None = None) -> None:
        """Read where each credential is set (`credentials_status`); `proceed` is called when answered."""
        request = CredentialsStatusRequest(action="credentials_status")
        self.executor.submit(request, partial(self._loaded, proceed))

    def type_value(self, name: str, typed: str) -> None:
        """Take what the user typed into a credential's field (empty: leave the stored value)."""
        if typed:
            self._typed[name] = SecretStr(typed)
        else:
            self._typed.pop(name, None)
        self.shown.errors.pop(name, None)
        self.shown.typed = frozenset(self._typed)
        self.shown.message = ""
        self.changed()

    def save(self, on_stored: Proceed | None = None) -> None:
        """Store the typed values (`store_credentials`, only those); a refused save keeps them.

        `on_stored` is called once a successful store's new status is read (the wizard moves on).
        """
        if not self.shown.can_save:
            return
        request = store_request(self._typed)
        if isinstance(request, ErrorResponse):
            errors = field_errors(request, CREDENTIALS_LOCATION, account_texts.credential_hint)
            self.shown.errors = errors or {}
            self.shown.message = tr("Fix the marked fields, then save again.")
            self.changed()
            return
        self.shown.saving = True
        self.shown.message = tr("Saving...")
        self.changed()
        sent = dict(self._typed)
        self.executor.submit(request, partial(self._stored, sent, on_stored))

    def test(self) -> None:
        """Try the credentials against R2 and Runpod (`check_credentials`; up to about half a minute)."""
        if self.shown.checking:
            return
        self.shown.checking = True
        self.shown.checks = ()
        self.changed()
        self.executor.submit(CheckCredentialsRequest(action="check_credentials"), self._checked)

    def discard_typed(self) -> None:
        """Forget what was typed and not stored (its fields are emptied: no value outlives its field)."""
        self._typed = {}
        self.shown.typed = frozenset()
        self.shown.errors = {}
        self.shown.message = ""
        self.changed()

    def _loaded(self, proceed: Proceed | None, response: Response) -> None:
        if isinstance(response, CredentialsStatusResponse):
            self.shown.status = response
            self.shown.load_error = ""
        elif isinstance(response, ErrorResponse):
            self.shown.load_error = error_message(response.error)
        self.changed()
        if proceed is not None:
            proceed()

    def _stored(self, sent: Typed, on_stored: Proceed | None, response: Response) -> None:
        self.shown.message = ""
        if isinstance(response, StoreCredentialsResponse):
            # Only what was sent is stored now; a value typed meanwhile stays typed.
            self._typed = _not_sent(self._typed, sent)
            self.shown.typed = frozenset(self._typed)
            self.shown.errors = {}
            self.shown.message = tr("Saved.")
            # Still saving until the new status is read: the wizard moves on only then, once.
            self.load(partial(self._reloaded, on_stored))
            self.changed()
            return
        self.shown.saving = False
        self.changed()
        if isinstance(response, ErrorResponse):
            self._hooks.on_error(error_view(response))

    def _reloaded(self, on_stored: Proceed | None) -> None:
        self.shown.saving = False
        self.changed()
        if on_stored is not None:
            on_stored()

    def _checked(self, response: Response) -> None:
        self.shown.checking = False
        if isinstance(response, CheckCredentialsResponse):
            self.shown.checks = tuple(map(_check_row, response.checks))
        self.changed()
        if isinstance(response, ErrorResponse):
            self._hooks.on_error(error_view(response))
