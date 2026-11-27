"""Validation and JSON Schema of the `Request` and `Response` unions."""

import json
from functools import cache
from typing import Literal

from pydantic import ConfigDict, JsonValue, TypeAdapter, ValidationError
from pydantic_core import ErrorDetails

from transcribe_ui.api.classes import ErrorBody, ErrorResponse, Request, Response
from transcribe_ui.api.constants import ERROR_INVALID_REQUEST

# Which union `render_schema` renders.
type SchemaKind = Literal["request", "response"]


@cache
def request_adapter() -> TypeAdapter[Request]:
    """Return the validator of the `Request` union; its errors never show the input (credentials)."""
    return TypeAdapter(Request, config=ConfigDict(hide_input_in_errors=True))


@cache
def response_adapter() -> TypeAdapter[Response]:
    """Return the validator and serializer of the `Response` union."""
    return TypeAdapter(Response)


def render_schema(kind: SchemaKind) -> str:
    """Return the JSON Schema of a union as stable, indented JSON text ending in a newline."""
    if kind == "request":
        document = request_adapter().json_schema(mode="validation")
    else:
        document = response_adapter().json_schema(mode="serialization")
    schema = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False)
    return f"{schema}\n"


def _problem(detail: ErrorDetails) -> JsonValue:
    location: list[JsonValue] = [str(part) for part in detail["loc"]]
    return {"location": location, "message": detail["msg"], "type": detail["type"]}


def parse_request(document: str | bytes) -> Request | ErrorResponse:
    """Validate a JSON request document; an invalid one gives its `invalid_request` response."""
    try:
        return request_adapter().validate_json(document)
    except ValidationError as error:
        return invalid_request(error)


def invalid_request(error: ValidationError) -> ErrorResponse:
    """Return the response to a request document that is not valid JSON or not a `Request`."""
    found = error.errors(include_url=False, include_context=False, include_input=False)
    problems: list[JsonValue] = [_problem(detail) for detail in found]
    message = f"The request is not valid ({len(problems)} problem(s))."
    return ErrorResponse(
        action=None, error=ErrorBody(code=ERROR_INVALID_REQUEST, message=message, details={"problems": problems})
    )
