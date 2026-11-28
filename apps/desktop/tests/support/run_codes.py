"""The codes of the run texts the Cloud page shows (stages, end reasons, worker states), from the backend's types."""

from typing import Any, get_args

from transcribe_contracts.classes import RunState
from transcribe_ui.i18n.constants import TextKind
from transcribe_ui.runs.classes import EndReason
from transcribe_ui.runs.constants import UNREADABLE_STATE
from transcribe_ui.runs.responses import RunStage


def _literal(alias: Any) -> set[str]:  # noqa: ANN401 - a `type` alias of a Literal
    return set(get_args(alias.__value__))


def run_text_codes() -> dict[TextKind, set[str]]:
    """Return the codes of each run text kind: run stages, end reasons, and the states a worker reports."""
    return {
        "run_stage": _literal(RunStage),
        "run_end_reason": _literal(EndReason),
        "worker_state": {*_literal(RunState), UNREADABLE_STATE},
    }
