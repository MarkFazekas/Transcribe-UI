"""Regression: no validator of a credential request echoes a value (plan m5, M5.5).

The `invalid_request` details omit inputs (§3.1), every custom validator message is a fixed
text, and `str(ValidationError)` hides the input too, so a refused credential cannot reach a
response, a log line or an exception message.
"""

import json

import pytest
from pydantic import ValidationError

from transcribe_ui.api.classes import ErrorResponse
from transcribe_ui.api.schema import parse_request, request_adapter
from transcribe_ui.credentials.classes import R2Credentials, StoreCredentialsRequest
from transcribe_ui.credentials.constants import VALUE_MASKED

# Part of every invalid value; must never show up in an error.
SENTINEL = "SENTINELvalue42"
# Repeats of the sentinel in a value longer than any accepted credential.
LONG_REPEATS = 200
# Credential fields of `store_credentials`.
FIELDS = ("r2_endpoint_url", "r2_bucket", "r2_access_key_id", "r2_secret_access_key", "runpod_api_key")
# Invalid values, each containing the sentinel (as text or inside another JSON type).
INVALID_VALUES = (
    f"{SENTINEL} {SENTINEL}",
    f"{SENTINEL}\t",
    f"{SENTINEL}\u0000",
    f"http://{SENTINEL}.example",
    f"https://user:{SENTINEL}@host.example",
    f"https://host.example/{SENTINEL}",
    f"https://host.example?{SENTINEL}",
    f"https://host.example:{SENTINEL}",
    f"https://[{SENTINEL}",
    f"Upper_{SENTINEL}",
    SENTINEL * LONG_REPEATS,
    [SENTINEL],
    {"value": SENTINEL},
)


def _document(field: str, candidate: object) -> str:
    return json.dumps({"action": "store_credentials", field: candidate})


def _invalid_cases() -> list[tuple[str, object]]:
    return [(field, candidate) for field in FIELDS for candidate in INVALID_VALUES]


def _validation_error(document: str) -> ValidationError | None:
    try:
        request_adapter().validate_json(document)
    except ValidationError as error:
        return error
    return None


@pytest.mark.parametrize(("field", "candidate"), _invalid_cases())
def test_refused_credential_is_never_echoed(field: str, candidate: object) -> None:
    """A refused value is absent from the response, the error text and every error message."""
    document = _document(field, candidate)
    error = _validation_error(document)
    if error is None:
        # A few values are valid for some fields (a URL or an upper-case text as a token).
        assert SENTINEL not in repr(request_adapter().validate_json(document))
        return
    assert SENTINEL not in str(error)
    for problem in error.errors(include_url=False, include_input=False, include_context=False):
        assert SENTINEL not in problem["msg"]
    response = parse_request(document)
    assert isinstance(response, ErrorResponse)
    assert SENTINEL not in response.model_dump_json()


@pytest.mark.parametrize("field", ["access_key_id", "secret_access_key", "bucket", "endpoint_url"])
def test_r2_credentials_errors_hide_the_input(field: str) -> None:
    """Credentials read from the keyring or the environment are validated without echoing either."""
    fields = {
        "endpoint_url": "https://r2.example.invalid",
        "bucket": "bucket",
        "access_key_id": "key",
        "secret_access_key": "secret",
        field: f"{SENTINEL} !",
    }
    with pytest.raises(ValidationError) as caught:
        R2Credentials.model_validate(fields)
    assert SENTINEL not in str(caught.value)


@pytest.mark.parametrize("field", ["r2_access_key_id", "r2_secret_access_key", "runpod_api_key"])
def test_masked_value_is_refused(field: str) -> None:
    """A `SecretStr` mask pasted back (only `*` characters) is refused, not stored."""
    error = _validation_error(_document(field, "**********"))
    assert error is not None
    assert [problem["msg"] for problem in error.errors()] == [f"Value error, {VALUE_MASKED}"]


def test_valid_request_hides_secrets_in_repr() -> None:
    """A valid request keeps its keys as `SecretStr`: neither `repr` nor a JSON dump shows them."""
    request = StoreCredentialsRequest.model_validate(
        {"action": "store_credentials", "r2_secret_access_key": f" {SENTINEL} ", "runpod_api_key": SENTINEL},
    )
    assert SENTINEL not in repr(request)
    assert SENTINEL not in request.model_dump_json()
    assert request.to_store() == {"r2_secret_access_key": SENTINEL, "runpod_api_key": SENTINEL}
