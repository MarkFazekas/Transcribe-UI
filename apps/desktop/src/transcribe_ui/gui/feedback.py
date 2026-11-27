"""Display data of error responses and confirmations, for the generic dialogs (architecture §3.1 "GUI").

Qt-free. Every error renders with one layout: the backend's message, then collapsed details
(the code, the details as rows and tables, "Open log folder"). A small table (`ERROR_FIXES`)
adds a button for codes that have a fix in the GUI; that table is presentation, the decision
stays the backend's, and an unknown code still renders. A `needs_confirmation` response renders
as one dialog for every action; accepting re-sends the same request with each confirmation's
field set to the value the backend names (`Confirmation.set_to`).
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydantic import JsonValue

from transcribe_ui.api.classes import Confirmation, ErrorResponse, NeedsConfirmation, Request
from transcribe_ui.api.constants import ERROR_INTERNAL
from transcribe_ui.api.schema import request_adapter
from transcribe_ui.gui.constants import CLEANUP_PENDING_BATCHES_KEY, ERROR_FIXES, FIX_LABELS, FixKind

# One object of a detail that lists objects.
type Record = Mapping[str, JsonValue]


@dataclass(frozen=True)
class DetailTable:
    """A detail holding a list of objects, as a table (overwritten targets, a launch plan, ...)."""

    title: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Details:
    """Structured details: one row per scalar or list of scalars, one table per list of objects."""

    rows: tuple[tuple[str, str], ...]
    tables: tuple[DetailTable, ...]


@dataclass(frozen=True)
class Fix:
    """A button that fixes an error: open a page (with an argument) or retry the request."""

    kind: FixKind
    label: str
    # The page's argument: the batch to open for `open_batch`; empty otherwise.
    argument: str = ""


@dataclass(frozen=True)
class ErrorView:
    """An error response as the error dialog or banner shows it."""

    message: str
    code: str
    details: Details
    fix: Fix | None
    # Whether to add "see the log" (`internal_error`).
    see_log: bool


@dataclass(frozen=True)
class ConfirmationItem:
    """One thing to confirm."""

    message: str
    details: Details


@dataclass(frozen=True)
class ConfirmationView:
    """A `needs_confirmation` response as the confirmation dialog shows it."""

    confirmations: tuple[ConfirmationItem, ...]


def render_detail(detail: JsonValue) -> str:
    """Return a detail as text: scalars as they are, a list one entry per line, objects as JSON."""
    if detail is None:
        return ""
    if isinstance(detail, bool):
        return "yes" if detail else "no"
    if isinstance(detail, list):
        return "\n".join(render_detail(entry) for entry in detail)
    if isinstance(detail, dict):
        return json.dumps(detail, ensure_ascii=False, sort_keys=True)
    return str(detail)


def _records(detail: JsonValue) -> list[dict[str, JsonValue]] | None:
    """Return a detail that is a non-empty list of objects, else None."""
    if not isinstance(detail, list) or not detail:
        return None
    records = [entry for entry in detail if isinstance(entry, dict)]
    return records if len(records) == len(detail) else None


def _columns(records: Sequence[Record]) -> tuple[str, ...]:
    # Every key of every record, in the order they first appear.
    seen: dict[str, None] = {}
    for record in records:
        seen.update(dict.fromkeys(record))
    return tuple(seen)


def _table(title: str, records: Sequence[Record]) -> DetailTable:
    columns = _columns(records)
    rows = [_row(record, columns) for record in records]
    return DetailTable(title, columns, tuple(rows))


def _row(record: Record, columns: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(render_detail(record.get(column)) for column in columns)


def details_view(details: Mapping[str, JsonValue]) -> Details:
    """Return structured details as rows and tables, in the backend's order."""
    rows: list[tuple[str, str]] = []
    tables: list[DetailTable] = []
    for key, detail in details.items():
        records = _records(detail)
        if records is None:
            rows.append((key, render_detail(detail)))
        else:
            tables.append(_table(key, records))
    return Details(tuple(rows), tuple(tables))


def _first_batch(details: Mapping[str, JsonValue]) -> str | None:
    batches = details.get(CLEANUP_PENDING_BATCHES_KEY)
    if isinstance(batches, list) and batches and isinstance(batches[0], str):
        return batches[0]
    return None


def _fix(code: str, details: Mapping[str, JsonValue]) -> Fix | None:
    kind = ERROR_FIXES.get(code)
    if kind is None:
        return None
    if kind != "open_batch":
        return Fix(kind, FIX_LABELS[kind])
    batch_id = _first_batch(details)
    return None if batch_id is None else Fix(kind, FIX_LABELS[kind], batch_id)


def error_view(response: ErrorResponse) -> ErrorView:
    """Return how an error response is shown."""
    error = response.error
    return ErrorView(
        message=error.message,
        code=error.code,
        details=details_view(error.details),
        fix=_fix(error.code, error.details),
        see_log=error.code == ERROR_INTERNAL,
    )


def confirmation_view(response: NeedsConfirmation) -> ConfirmationView:
    """Return how a `needs_confirmation` response is shown."""
    return ConfirmationView(tuple(_confirmation_item(asked) for asked in response.confirmations))


def _confirmation_item(confirmation: Confirmation) -> ConfirmationItem:
    return ConfirmationItem(confirmation.message, details_view(confirmation.details))


def confirmed_request(request: Request, response: NeedsConfirmation) -> Request:
    """Return the same request with each confirmation's field set to its `set_to` (validated).

    The backend names the confirming value (`true` for a flag, the plan's fingerprint for
    `confirm_fingerprint`), so every frontend confirms the same way and decides nothing.
    """
    document = request.model_dump(mode="json")
    for confirmation in response.confirmations:
        document[confirmation.field] = confirmation.set_to
    return request_adapter().validate_python(document)
