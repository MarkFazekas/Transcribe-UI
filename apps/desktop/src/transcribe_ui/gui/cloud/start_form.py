"""What the start dialog shows and the user chose: the plan, test mode, the wait for a machine (Qt-free).

A changed plan comes back inside the launch's `needs_confirmation`: its details hold the plan,
and its `set_to` the fingerprint the next Start confirms.
"""

import logging
from dataclasses import dataclass

from pydantic import ValidationError

from transcribe_ui.api.classes import Confirmation, NeedsConfirmation
from transcribe_ui.gui.cloud.constants import PLAN_DETAIL
from transcribe_ui.gui.cloud.plan_display import PlanView, plan_view, wait_choices
from transcribe_ui.runs.classes import LaunchPlan, LaunchWorkerRequest, PlanLaunchRequest, WaitBounds
from transcribe_ui.runs.constants import CONFIRM_FINGERPRINT_FIELD, WAIT_DEFAULT_S

logger = logging.getLogger(__name__)


def asked_plan(response: NeedsConfirmation) -> tuple[LaunchPlan, str] | None:
    """Return the plan a launch confirmation asks for and its fingerprint (None, logged, if it has none)."""
    for asked in response.confirmations:
        if asked.field == CONFIRM_FINGERPRINT_FIELD and isinstance(asked.set_to, str):
            return _plan_of(asked)
    logger.error("The launch confirmation names no fingerprint")
    return None


def _plan_of(asked: Confirmation) -> tuple[LaunchPlan, str] | None:
    try:
        plan = LaunchPlan.model_validate(asked.details.get(PLAN_DETAIL))
    except ValidationError:
        logger.exception("The launch confirmation does not hold a plan")
        return None
    return plan, str(asked.set_to)


@dataclass
class StartForm:
    """What the start dialog shows and the user chose: the plan, test mode, the wait for a machine."""

    plan: LaunchPlan | None = None
    view: PlanView | None = None
    test_mode: bool = False
    wait_s: int = WAIT_DEFAULT_S
    # Whether the shown plan replaced the one the user saw at the last Start.
    plan_changed: bool = False
    # The fingerprint Start confirms: the plan's, or the `set_to` of the confirmation of a changed plan.
    fingerprint: str | None = None
    # Whether the user chose the wait (else the default the backend names applies).
    wait_chosen: bool = False

    def wait_choices(self) -> tuple[tuple[int, str], ...]:
        """Return the waits for a machine to offer (seconds, label), within the backend's bounds."""
        return wait_choices(WaitBounds() if self.plan is None else self.plan.wait)

    def choose_wait(self, seconds: int) -> None:
        """Choose how long Runpod may wait for a free machine."""
        self.wait_s = seconds
        self.wait_chosen = True

    def plan_request(self) -> PlanLaunchRequest:
        """Return the plan request as the dialog is set now."""
        return PlanLaunchRequest(action="plan_launch", test_mode=self.test_mode)

    def launch_request(self) -> LaunchWorkerRequest:
        """Return the launch request as the dialog is set now (confirming the plan shown)."""
        return LaunchWorkerRequest(
            action="launch_worker",
            wait_s=self.wait_s,
            test_mode=self.test_mode,
            confirm_fingerprint=self.fingerprint,
        )

    def show(self, plan: LaunchPlan, fingerprint: str, *, changed: bool) -> None:
        """Show a plan (`changed`: it replaces the one confirmed); keep the chosen wait while it fits."""
        self.plan = plan
        self.view = plan_view(plan)
        self.test_mode = plan.test_mode
        self.fingerprint = fingerprint
        self.plan_changed = changed
        bounds = plan.wait
        if not self.wait_chosen or not bounds.min_s <= self.wait_s <= bounds.max_s:
            self.wait_s = bounds.default_s
