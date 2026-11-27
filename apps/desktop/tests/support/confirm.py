"""Confirming a request the way every frontend does: each asked field set to its `set_to`."""

import json

from transcribe_ui.api.classes import NeedsConfirmation, Response


def confirmed_document(document: str, response: Response) -> str:
    """Return the request document again with every field the `needs_confirmation` answer asks for set."""
    assert isinstance(response, NeedsConfirmation), response
    fields = json.loads(document)
    for confirmation in response.confirmations:
        fields[confirmation.field] = confirmation.set_to
    return json.dumps(fields)
