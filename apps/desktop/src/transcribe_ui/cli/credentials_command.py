"""`transcribe-ui credentials`: store credentials typed at the terminal (architecture §3.1).

The values never sit in a file or in the shell history. Each credential is asked for in turn:
the endpoint URL and the bucket with echo, the keys and the secret without; an empty answer
skips it (what is stored stays). Each answer is checked at once by the request's own validation
and asked for again when it is refused, so a typo does not lose the answers after it. The
answers become the same `store_credentials` request document a request file would hold, so the
response, the exit code and what is stored equal `run` with that file.
"""

import json
from collections.abc import Mapping
from typing import BinaryIO

from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import ErrorResponse, Request
from transcribe_ui.api.schema import parse_request
from transcribe_ui.cli.constants import (
    ECHOED_CREDENTIALS,
    ERROR_INVALID_ARGUMENTS,
    NOT_A_TERMINAL_MESSAGE,
    STORE_CREDENTIALS_ACTION,
)
from transcribe_ui.cli.output import failure, respond
from transcribe_ui.cli.prompt import Prompt
from transcribe_ui.cli.run_command import cancelled_before_reading, execute_request
from transcribe_ui.credentials.classes import CredentialName
from transcribe_ui.credentials.constants import CREDENTIAL_ORDER
from transcribe_ui.i18n.catalog import tr
from transcribe_ui.i18n.credential_texts import credential_labels, credential_rules


def _request(answers: Mapping[CredentialName, str]) -> Request | ErrorResponse:
    """Validate the answers as the `store_credentials` request document a file would hold."""
    return parse_request(json.dumps({"action": STORE_CREDENTIALS_ACTION, **answers}))


def answer_accepted(name: CredentialName, answer: str) -> bool:
    """Return whether the answer skips the credential (blank) or is a valid value for it."""
    if not answer.strip():
        return True
    return not isinstance(_request({name: answer}), ErrorResponse)


def _ask_until_valid(prompt: Prompt, name: CredentialName, label: str, rule: str) -> str:
    """Ask for one credential until the answer is empty or valid; return it."""
    ask = prompt.ask if name in ECHOED_CREDENTIALS else prompt.ask_hidden
    while True:
        answer = ask(label)
        if answer_accepted(name, answer):
            return answer
        prompt.tell(rule)


def _answers(prompt: Prompt) -> dict[CredentialName, str]:
    """Ask for each credential in order; an empty (or blank) answer is left out."""
    labels = credential_labels()
    rules = credential_rules()
    answers: dict[CredentialName, str] = {}
    for name in CREDENTIAL_ORDER:
        answer = _ask_until_valid(prompt, name, labels[name], rules[name])
        if answer.strip():
            answers[name] = answer
    return answers


def store_typed_credentials(prompt: Prompt, backend: Backend, stdout: BinaryIO) -> int:
    """Ask for the credentials, store them through `store_credentials`, print the response, return the exit code.

    Refused with `invalid_arguments` when stdin is not a terminal (`getpass` would read the
    values from the pipe instead, with echo). Ctrl+C or the end of input (Ctrl+D, Ctrl+Z) while
    asking stores nothing and answers `cancelled`.
    """
    if not prompt.interactive():
        return respond(stdout, failure(ERROR_INVALID_ARGUMENTS, NOT_A_TERMINAL_MESSAGE))
    prompt.tell(tr("Type each credential and press Enter; the keys and the secret are not shown as you type."))
    prompt.tell(tr("Press Enter alone to keep what is stored."))
    try:
        answers = _answers(prompt)
    except EOFError:
        return respond(stdout, cancelled_before_reading())
    request = _request(answers)
    if isinstance(request, ErrorResponse):
        return respond(stdout, request)
    return execute_request(request, backend, stdout, None)
