"""Credential store: keyring entries, with environment variables as the fallback (architecture §3.6).

A credential comes from the keyring when it has an entry, else from its environment variable
(`ENVIRONMENT_VARIABLES`, the names of `.env`). Values never leave this module except to the
client that needs them: errors and logs name credentials and variables, never values.
"""

import logging
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from typing import Protocol

from keyring.errors import NoKeyringError
from pydantic import JsonValue, SecretStr, TypeAdapter, ValidationError

from transcribe_ui.api.errors import Details, DomainError
from transcribe_ui.credentials.classes import (
    CredentialName,
    CredentialSource,
    CredentialStatus,
    KeyringState,
    R2Credentials,
    Token,
)
from transcribe_ui.credentials.constants import (
    CREDENTIAL_ORDER,
    ENVIRONMENT_VARIABLES,
    ERROR_CREDENTIALS_INVALID,
    ERROR_CREDENTIALS_MISSING,
    ERROR_KEYRING_FAILED,
    ERROR_KEYRING_UNAVAILABLE,
    KEYRING_SERVICE,
    R2_CREDENTIALS,
    RUNPOD_API_KEY,
)

logger = logging.getLogger(__name__)


class SecretBackend(Protocol):
    """The part of a `keyring` backend the store uses (the method names are keyring's)."""

    def get_password(self, service: str, username: str) -> str | None:  # noqa: WPS615 -- keyring's API.
        """Return the stored value, or None."""
        ...

    def set_password(self, service: str, username: str, password: str) -> None:  # noqa: WPS615 -- keyring's API.
        """Store the value."""
        ...


# Keyring entries that hold a value, by credential name.
type StoredEntries = dict[CredentialName, str]
# Returns the keyring backend (production: `keyring.get_keyring`); called once, on first use.
type KeyringFactory = Callable[[], SecretBackend]


@dataclass(frozen=True)
class Found:
    """A credential and where it came from."""

    secret: SecretStr
    source: CredentialSource


@dataclass(frozen=True)
class Lookup:
    """The credentials found for a set of names, and the keyring's state while reading them."""

    keyring: KeyringState
    found: Mapping[CredentialName, Found]

    def source(self, name: CredentialName) -> CredentialSource:
        """Return where the credential came from ("missing" when nowhere)."""
        entry = self.found.get(name)
        return "missing" if entry is None else entry.source

    def text(self, name: CredentialName) -> str:
        """Return the credential in plain text ("" when missing)."""
        entry = self.found.get(name)
        return "" if entry is None else entry.secret.get_secret_value()

    def require(self, names: Iterable[CredentialName]) -> None:
        """Check that every named credential was found.

        Raises:
            DomainError: `credentials_missing`, naming the credentials and their variables.
        """
        missing = [name for name in names if name not in self.found]
        if missing:
            raise _missing_error(missing, self.keyring)


def _json_list(texts: Iterable[str]) -> list[JsonValue]:
    return [str(text) for text in texts]


def _variables(names: Iterable[CredentialName]) -> list[str]:
    return [ENVIRONMENT_VARIABLES[name] for name in names]


def _missing_error(missing: list[CredentialName], keyring: KeyringState) -> DomainError:
    variables = ", ".join(_variables(missing))
    if keyring == "available":
        advice = f"Store them with store_credentials or set the environment variables {variables}."
    else:
        advice = f"The keyring cannot be used ({keyring}); set the environment variables {variables}."
    message = f"Credentials not set: {', '.join(missing)}. {advice}"
    details: Details = {
        "missing": _json_list(missing),
        "variables": _json_list(_variables(missing)),
        "keyring": keyring,
    }
    return DomainError(ERROR_CREDENTIALS_MISSING, message, details)


def _no_keyring_error(names: Iterable[CredentialName], written: list[CredentialName]) -> DomainError:
    variables = _variables(names)
    joined = ", ".join(variables)
    message = f"This system has no keyring to store credentials in. Set the environment variables {joined} instead."
    details: Details = {"stored": _json_list(written), "variables": _json_list(variables)}
    return DomainError(ERROR_KEYRING_UNAVAILABLE, message, details)


def _keyring_failed_error(reason: str, written: list[CredentialName]) -> DomainError:
    message = f"The keyring refused to store the credentials ({reason}). Unlock it and try again, or use the environment variables."
    return DomainError(ERROR_KEYRING_FAILED, message, {"stored": _json_list(written), "reason": reason})


class CredentialStore:
    """Reads and writes the app's credentials; safe to use from several threads."""

    def __init__(self, keyring: KeyringFactory, environ: Mapping[str, str]) -> None:
        """Use the keyring backend `keyring` returns (on first use) and this environment."""
        self._make_keyring = keyring
        self._environ = environ
        self._lock = threading.Lock()
        self._backend: SecretBackend | None = None

    def lookup(self, names: Iterable[CredentialName] = CREDENTIAL_ORDER) -> Lookup:
        """Return the named credentials: the keyring entry if set, else the environment variable."""
        wanted = tuple(names)
        keyring, stored = self._read_keyring(wanted)
        found: dict[CredentialName, Found] = {}
        for name in wanted:
            from_keyring = stored.get(name, "")
            from_environment = self._environ.get(ENVIRONMENT_VARIABLES[name], "").strip()
            if from_keyring:
                found[name] = Found(SecretStr(from_keyring), "keyring")
            elif from_environment:
                found[name] = Found(SecretStr(from_environment), "environment")
        return Lookup(keyring, found)

    def statuses(self) -> tuple[KeyringState, tuple[CredentialStatus, ...]]:
        """Return the keyring's state and, per credential, where it is set (never the value)."""
        lookup = self.lookup()
        statuses = tuple(_status(name, lookup) for name in CREDENTIAL_ORDER)
        return lookup.keyring, statuses

    def store(self, credentials: Mapping[CredentialName, str]) -> tuple[CredentialName, ...]:
        """Write the credentials to the keyring, in order; return the names written.

        Raises:
            DomainError: `keyring_unavailable` (no backend; the message names the environment
                variables to use instead) or `keyring_failed`; the details list what was
                written before the failure.
        """
        written: list[CredentialName] = []
        try:
            self._write(credentials, written)
        except NoKeyringError as error:
            raise _no_keyring_error(credentials, written) from error
        except Exception as error:  # Keyring backends raise their own types (D-Bus, Windows API).
            reason = type(error).__name__
            logger.warning("Storing credentials in the keyring failed (%s)", reason)
            raise _keyring_failed_error(reason, written) from error
        return tuple(written)

    def r2(self) -> R2Credentials:
        """Return the R2 credentials.

        Raises:
            DomainError: `credentials_missing` or `credentials_invalid` (names, never values).
        """
        lookup = self.lookup(R2_CREDENTIALS)
        lookup.require(R2_CREDENTIALS)
        fields = {_r2_field(name): lookup.text(name) for name in R2_CREDENTIALS}
        try:
            return R2Credentials.model_validate(fields)
        except ValidationError as error:
            invalid: list[CredentialName] = [name for name in R2_CREDENTIALS if _names_field(error, _r2_field(name))]
            raise _invalid_error(invalid, lookup) from None

    def runpod_api_key(self) -> SecretStr:
        """Return the Runpod API key.

        Raises:
            DomainError: `credentials_missing` or `credentials_invalid` (names, never values).
        """
        lookup = self.lookup((RUNPOD_API_KEY,))
        lookup.require((RUNPOD_API_KEY,))
        try:
            return _token_adapter().validate_python(lookup.text(RUNPOD_API_KEY))
        except ValidationError:
            raise _invalid_error([RUNPOD_API_KEY], lookup) from None

    def _keyring(self) -> SecretBackend:
        with self._lock:
            if self._backend is None:
                self._backend = self._make_keyring()
            return self._backend

    def _write(self, credentials: Mapping[CredentialName, str], written: list[CredentialName]) -> None:
        backend = self._keyring()
        for name, secret in credentials.items():
            backend.set_password(KEYRING_SERVICE, name, secret)
            written.append(name)

    def _read_keyring(self, names: tuple[CredentialName, ...]) -> tuple[KeyringState, StoredEntries]:
        try:
            stored = self._read_entries(names)
        except NoKeyringError:
            return "unavailable", {}
        except Exception as error:  # noqa: BLE001 -- keyring backends raise their own types (D-Bus, Windows API); any of them means the keyring failed.
            logger.warning("Reading the keyring failed (%s); using the environment", type(error).__name__)
            return "failed", {}
        return "available", stored

    def _read_entries(self, names: tuple[CredentialName, ...]) -> StoredEntries:
        backend = self._keyring()
        entries: StoredEntries = {}
        for name in names:
            entry = (backend.get_password(KEYRING_SERVICE, name) or "").strip()
            if entry:
                entries[name] = entry
        return entries


def _status(name: CredentialName, lookup: Lookup) -> CredentialStatus:
    environment_variable = ENVIRONMENT_VARIABLES[name]
    return CredentialStatus(name=name, environment_variable=environment_variable, source=lookup.source(name))


def _r2_field(name: CredentialName) -> str:
    """Return the `R2Credentials` field of an R2 credential name."""
    return name.removeprefix("r2_")


@cache
def _token_adapter() -> TypeAdapter[SecretStr]:
    """Return the validator of a single token (the Runpod API key)."""
    return TypeAdapter(Token)


def _names_field(error: ValidationError, field: str) -> bool:
    locations = [problem["loc"] for problem in error.errors(include_input=False)]
    return any(location[:1] == (field,) for location in locations)


def _described(name: CredentialName, source: CredentialSource) -> str:
    return f"{name} (from the {source})"


def _invalid_error(invalid: list[CredentialName], lookup: Lookup) -> DomainError:
    sources = [lookup.source(name) for name in invalid]
    described = ", ".join(map(_described, invalid, sources))
    message = f"Credentials not valid: {described}. Store them again or fix the environment variables."
    return DomainError(
        ERROR_CREDENTIALS_INVALID, message, {"invalid": _json_list(invalid), "sources": _json_list(sources)}
    )
