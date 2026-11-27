"""Credentials the Runpod requests cannot use (architecture §3.6; plan m5 M5.6).

No test talks to the real Runpod API or to R2: invalid values are refused before any client
exists, and a service without credentials reports "missing".
"""

import logging
from pathlib import Path

import pytest

from support.runpod import FULL_CREDENTIALS, RUNPOD_ONLY, TEST_R2, Cloud
from transcribe_ui.api.classes import ErrorResponse
from transcribe_ui.api.ports import CancelFlag, DiscardProgress, RequestContext
from transcribe_ui.credentials.classes import CheckCredentialsRequest
from transcribe_ui.credentials.constants import ERROR_CREDENTIALS_INVALID
from transcribe_ui.credentials.services import check_credentials

# A credential with a non-ASCII character (httpx cannot put it into a header).
NON_ASCII_KEY = "rpa_KEY\u00e9_NON_ASCII"


@pytest.mark.parametrize("name", ["runpod_api_key", "r2_secret_access_key"])
def test_non_ascii_credentials_are_invalid(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    name: str,
) -> None:
    """A stored non-ASCII key is `credentials_invalid` for the setup and the check, never a crash or a log."""
    caplog.set_level(logging.DEBUG)
    account = Cloud(credentials={**FULL_CREDENTIALS, name: NON_ASCII_KEY}, root=tmp_path / "setup")
    setup = account.set_up()
    assert isinstance(setup, ErrorResponse)
    # Only the service in question has credentials, so the other check reports "missing" and
    # no check reaches a network (the invalid value is refused before any client exists).
    service_credentials = RUNPOD_ONLY if name == "runpod_api_key" else TEST_R2
    checker = Cloud(credentials={**service_credentials, name: NON_ASCII_KEY})
    context = RequestContext(checker.ports(tmp_path / "check"), DiscardProgress(), CancelFlag())
    response = check_credentials(CheckCredentialsRequest(action="check_credentials"), context)
    outcomes = {str(check.service): (check.outcome, check.code) for check in response.checks}
    service = "runpod" if name == "runpod_api_key" else "r2"
    expected: tuple[object, ...] = (ERROR_CREDENTIALS_INVALID, ("failed", ERROR_CREDENTIALS_INVALID), [], [])
    assert (setup.error.code, outcomes.get(service), account.api.seen(), checker.api.seen()) == expected
    shown = f"{setup.model_dump_json()} {response.model_dump_json()} {caplog.text}"
    assert NON_ASCII_KEY not in shown
    assert shown.isascii()
