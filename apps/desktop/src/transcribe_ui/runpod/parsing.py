"""Parsing of Runpod response bodies; validation messages are dropped (they echo data)."""

from contextlib import suppress

import httpx
from pydantic import BaseModel, JsonValue, ValidationError

from transcribe_ui.runpod.calls import Call
from transcribe_ui.runpod.errors import AmbiguousCreateError, RunpodApiError


def parse_body[ModelT: BaseModel](call: Call, response: httpx.Response, model: type[ModelT]) -> ModelT:
    """Return the response body as the model.

    Raises:
        AmbiguousCreateError: A create succeeded but its body does not parse: the object exists.
        RunpodApiError: The body does not match (the validation message is dropped: it echoes data).
    """
    with suppress(ValidationError):
        return model.model_validate_json(response.content)
    msg = f"{call.label}: unexpected response shape"
    error_type = AmbiguousCreateError if call.creates else RunpodApiError
    raise error_type(msg, status=response.status_code, scope=call.scope)


def validate_entry[ModelT: BaseModel](call: Call, entry: JsonValue, model: type[ModelT]) -> ModelT:
    """Return one raw list entry as the model.

    Raises:
        RunpodApiError: It does not match (the validation message is dropped: it echoes data).
    """
    with suppress(ValidationError):
        return model.model_validate(entry)
    msg = f"{call.label}: unexpected response shape"
    raise RunpodApiError(msg, scope=call.scope)
