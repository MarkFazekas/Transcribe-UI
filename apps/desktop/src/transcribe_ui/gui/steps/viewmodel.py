"""The step header's display data (plan m6 §1 "Layout": 1 Files → 2 Review & upload → 3 → 4).

Qt-free. A job's step comes from the backend (`BatchSummary.stage`); the header only renders
it: the steps before it are done, it is current, the ones after it are upcoming, and the stage
`done` is past every step. The step a page shows can differ from the job's (after Back).
"""

from dataclasses import dataclass

from transcribe_ui.batches.responses import BatchStage
from transcribe_ui.gui.steps.constants import STEPS, StepState
from transcribe_ui.i18n.texts import code_text


@dataclass(frozen=True)
class StepView:
    """One step of the header."""

    key: str
    number: int
    title: str
    state: StepState
    # Whether the page shows this step now.
    shown: bool


def step_views(
    stage: BatchStage | None,
    shown: str | None = None,
) -> tuple[StepView, ...]:
    """Return the steps for a job at `stage` (None: no job yet, at the first step) showing `shown` (default: its step)."""
    current = _current(stage)
    shown_key = _key_at(current) if shown is None else shown
    steps = []
    for index, _key in enumerate(STEPS):
        steps.append(_step(index, current, shown_key))
    return tuple(steps)


def _step(index: int, current: int, shown_key: str | None) -> StepView:
    key = STEPS[index]
    return StepView(
        key=key,
        number=index + 1,
        title=code_text("batch_stage", key),
        state=_state(index, current),
        shown=key == shown_key,
    )


def _current(stage: BatchStage | None) -> int:
    """Return the index of the job's step: the first without a job, past the last once `done`."""
    if stage is None:
        return 0
    return STEPS.index(stage) if stage in STEPS else len(STEPS)


def _key_at(index: int) -> str | None:
    return STEPS[index] if index < len(STEPS) else None


def _state(index: int, current: int) -> StepState:
    if index < current:
        return "done"
    return "current" if index == current else "upcoming"
