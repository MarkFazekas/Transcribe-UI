"""Request documents of the settings forms, and the fields an `invalid_request` response refuses (Qt-free).

A form's document is validated as the backend validates a request document
(`api.schema.parse_request`), so an invalid one gets exactly the `invalid_request` response the
backend and the CLI give for it, without a round trip; its problems are shown at their fields.
"""

import json
from collections.abc import Callable, Mapping, Sequence

from pydantic import JsonValue

from transcribe_ui.api.classes import ErrorResponse, Request
from transcribe_ui.api.constants import ERROR_INVALID_REQUEST
from transcribe_ui.api.schema import parse_request
from transcribe_ui.gui.settings.constants import FORM_FIELD, NESTED_FIELD

# Returns how to fix a field (by its key).
type Hint = Callable[[str], str]
# How to fix each refused field, by field key.
type FieldErrors = dict[str, str]


def validated(document: Mapping[str, JsonValue]) -> Request | ErrorResponse:
    """Return the request of a document, or the `invalid_request` response the backend gives for it."""
    return parse_request(json.dumps(document))


def _field(location: Sequence[JsonValue], prefix: Sequence[str]) -> str | None:
    """Return the field key of a problem's location under `prefix`; None for a location elsewhere."""
    parts = [str(part) for part in location]
    start = len(prefix)
    if parts[:start] != list(prefix):
        return None
    rest = parts[start:]
    if not rest:
        return FORM_FIELD
    if rest[0] == NESTED_FIELD and len(rest) > 1:
        return rest[1]
    return rest[0]


def _locations(response: ErrorResponse) -> list[Sequence[JsonValue]]:
    problems = response.error.details.get("problems")
    if not isinstance(problems, list):
        return []
    locations: list[Sequence[JsonValue]] = []
    for problem in problems:
        if isinstance(problem, dict):
            location = problem.get("location")
            if isinstance(location, list):
                locations.append(location)
    return locations


def field_errors(response: ErrorResponse, prefix: Sequence[str], hint: Hint) -> FieldErrors | None:
    """Return the refused fields of an `invalid_request` response with how to fix each; None for other errors.

    A problem outside the form's fields counts for the whole form (`FORM_FIELD`).
    """
    if response.error.code != ERROR_INVALID_REQUEST:
        return None
    errors: FieldErrors = {}
    for location in _locations(response):
        field = _field(location, prefix)
        key = FORM_FIELD if field is None else field
        errors.setdefault(key, hint(key))
    if not errors:
        errors[FORM_FIELD] = hint(FORM_FIELD)
    return errors
