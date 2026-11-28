"""View-model of the start dialog: plan, wait, test mode, Start (§3.1 "GUI"; step 3 of a job).

Qt-free. Opening it plans (`plan_launch`, latest wins: a change of the test mode plans again):
the jobs ready, the audio length, the cost range and the time limit, and the jobs of other
computers, each with a "Delete from cloud" button that only emits `delete_unknown_batch`
(`plan_invalidated` plans again). Start sends `launch_worker` with the chosen wait, the test
mode and the plan's fingerprint. When the backend's plan changed meanwhile, it answers with the
new plan to confirm (`needs_confirmation`): the dialog shows it, says that it changed, and the
next Start confirms it with the confirmation's `set_to`. An error (`cleanup_pending` with its
link to the job, a run already active, the cloud unreachable) shows in the dialog with its fix
and Retry, which sends the failed request again (the plan or the launch). The launch's answer
reaches the run list even when the dialog was closed meanwhile; while a Start is in flight a
reopened dialog cannot start again. A changed plan answered after the dialog was closed reopens
it with that plan (nothing was started, and the user meant to start); an error then opens the
error dialog. An earlier opening's Start that answers into a reopened dialog only leaves a note
there (or the error dialog), and never closes it.
"""

from collections.abc import Callable
from functools import partial

from transcribe_ui.api.classes import ErrorResponse, NeedsConfirmation, Response
from transcribe_ui.api.constants import ERROR_CANCELLED
from transcribe_ui.gui.cloud.constants import LAUNCH_KEY, PLAN_KEY, StartPhase, StartStep
from transcribe_ui.gui.cloud.ports import CloudOutlet
from transcribe_ui.gui.cloud.start_form import StartForm, asked_plan
from transcribe_ui.gui.executor import ExclusiveChannel, Executor, LatestChannel
from transcribe_ui.gui.feedback import ErrorView, confirmation_mismatch, error_view
from transcribe_ui.gui.viewmodel import ViewModel
from transcribe_ui.i18n.catalog import tr
from transcribe_ui.runs.responses import LaunchWorkerResponse, PlanLaunchResponse

# Called with the answer of a launch that started a run.
type Launched = Callable[[LaunchWorkerResponse], None]


class StartModel(ViewModel):
    """The start dialog: the plan, the wait for a machine, test mode, and Start."""

    def __init__(
        self,
        executor: Executor,
        outlet: CloudOutlet,
        launched: Launched,
        run_active: Callable[[], bool],
    ) -> None:
        """Send through `executor`; errors after a close go to `outlet`; a started run goes to `launched`.

        `run_active` tells whether a listed run is not final: Start waits until it is (the
        backend would refuse it with `run_active`).
        """
        super().__init__(executor)
        self.run_active = run_active
        self._plans = LatestChannel(executor)
        self._launch = ExclusiveChannel(executor)
        self._outlet = outlet
        self._launched = launched
        self.phase: StartPhase = "closed"
        self.form = StartForm()
        self.error: ErrorView | None = None
        # A note about a Start sent from an earlier opening of the dialog, or "".
        self.notice = ""
        # Whether the model opened the dialog itself (a Start answered with a changed plan after a close).
        self.reopened = False
        # Which request failed last (Retry sends it again), or None.
        self._failed: StartStep | None = None
        # Each opening of the dialog has its own number; a launch answers the opening it was sent from.
        self._opening = 0

    @property
    def can_start(self) -> bool:
        """Return whether Start applies: a plan with work is shown, no Start is in flight, no run is active."""
        view = self.form.view
        shown = self.phase == "ready" and view is not None and not view.empty
        return shown and not self._launch.busy(LAUNCH_KEY) and not self.run_active()

    def show_dialog(self) -> None:
        """Open the dialog fresh (no test mode, the default wait) and plan."""
        self._opening += 1
        self.form = StartForm()
        self.error = None
        self.reopened = False
        self.notice = tr("Your earlier Start is still being sent.") if self._launch.busy(LAUNCH_KEY) else ""
        self.phase = "planning"
        self.plan_again()

    def close(self) -> None:
        """Close the dialog; a launch in flight still reaches the run list."""
        self.phase = "closed"
        self.changed()

    def plan_again(self, *, test_mode: bool | None = None) -> None:
        """Plan again while the dialog is open and not starting: test mode changed, a job deleted, Retry."""
        same_mode = test_mode is not None and test_mode == self.form.test_mode
        if same_mode or self.phase not in {"planning", "ready"}:
            return
        if test_mode is not None:
            self.form.test_mode = test_mode
        self.error = None
        self._failed = None
        self.phase = "planning"
        self._plans.send(PLAN_KEY, self.form.plan_request(), self._planned)
        self.changed()

    def retry(self) -> None:
        """Send the request that failed again: the launch after a failed Start, else the plan."""
        self.notice = ""
        if self._failed == "launch" and self.form.plan is not None:
            self.error = None
            self.start()
        else:
            self.plan_again()

    def start(self) -> None:
        """Start the cloud machine on the plan shown (one launch at a time)."""
        if not self.can_start:
            return
        # Before the send: an executor may answer inside it.
        self.phase = "launching"
        self.error = None
        self.notice = ""
        self.reopened = False
        self._failed = None
        self.changed()
        answered = partial(self._answered, self._opening)
        self._launch.send(LAUNCH_KEY, self.form.launch_request(), answered)

    def _planned(self, response: Response) -> None:
        if self.phase != "planning":
            # Closed meanwhile: this plan is not shown.
            return
        self.phase = "ready"
        if isinstance(response, PlanLaunchResponse):
            plan = response.plan
            self.form.show(plan, plan.fingerprint, changed=False)
        elif isinstance(response, ErrorResponse) and response.error.code != ERROR_CANCELLED:
            self.error = error_view(response)
            self._failed = "plan"
        self.changed()

    def _answered(self, opening: int, response: Response) -> None:
        if isinstance(response, LaunchWorkerResponse):
            if opening == self._opening:
                self.phase = "launched"
            else:
                self.notice = tr("The cloud machine of your earlier Start is starting; the Cloud page shows it.")
                # Its plan is covered by that run now.
                self.plan_again()
            self.changed()
            self._launched(response)
            return
        error = None
        if isinstance(response, ErrorResponse) and response.error.code != ERROR_CANCELLED:
            error = error_view(response)
        asked = isinstance(response, NeedsConfirmation)
        if opening != self._opening or (self.phase == "closed" and not asked):
            self._not_started(error, asked=asked)
            return
        # The Start of this opening (reopened if it was closed: a changed plan asks to confirm again).
        self.reopened = self.phase == "closed"
        self.phase = "ready"
        self.error = error
        self._failed = None if error is None else "launch"
        if isinstance(response, NeedsConfirmation):
            changed_plan = asked_plan(response)
            if changed_plan is None:
                self.error = confirmation_mismatch(self.form.launch_request())
            else:
                self.form.show(*changed_plan, changed=True)
        self.changed()

    def _not_started(self, error: ErrorView | None, *, asked: bool) -> None:
        """A Start from an earlier opening, or from a closed dialog, started nothing: say so.

        An error goes to the window's error dialog. A changed plan plans again here, reopening
        the dialog when it was closed meanwhile (the user meant to start).
        """
        self.notice = ""
        if error is not None:
            self._outlet.show_error(error, None)
        elif asked:
            if self.phase == "closed":
                self.reopened = True
                self.phase = "planning"
            self.plan_again()
            self.notice = tr("Your earlier Start started nothing, as the work changed; check the plan here.")
        self.changed()
