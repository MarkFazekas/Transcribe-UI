"""View-model of the start dialog: plan, wait, test mode, Start (§3.1 "GUI"; step 3 of a job).

Qt-free. Opening it plans (`plan_launch`, latest wins: a change of the test mode plans again):
the jobs ready, the audio length, the cost range and the time limit, and the jobs of other
computers, each with a "Delete from cloud" button that only emits `delete_unknown_batch`
(`plan_invalidated` plans again). Start sends `launch_worker` with the chosen wait, the test
mode and the plan's fingerprint. When the backend's plan changed meanwhile, it answers with the
new plan to confirm (`needs_confirmation`): the dialog shows it, says that it changed, and the
next Start confirms it with the confirmation's `set_to`. An error (`cleanup_pending` with its
link to the job, a run already active, the cloud unreachable) shows in the dialog with its fix
and Retry. The launch's answer reaches the run list even when the dialog was closed meanwhile.
"""

from collections.abc import Callable

from transcribe_ui.api.classes import ErrorResponse, NeedsConfirmation, Response
from transcribe_ui.api.constants import ERROR_CANCELLED
from transcribe_ui.gui.cloud.commands import internal_error
from transcribe_ui.gui.cloud.constants import LAUNCH_KEY, PLAN_KEY, StartPhase
from transcribe_ui.gui.cloud.ports import CloudOutlet
from transcribe_ui.gui.cloud.start_form import StartForm, asked_plan
from transcribe_ui.gui.executor import ExclusiveChannel, Executor, LatestChannel
from transcribe_ui.gui.feedback import ErrorView, error_view
from transcribe_ui.gui.viewmodel import ViewModel
from transcribe_ui.runs.responses import LaunchWorkerResponse, PlanLaunchResponse

# Called with the answer of a launch that started a run.
type Launched = Callable[[LaunchWorkerResponse], None]


class StartModel(ViewModel):
    """The start dialog: the plan, the wait for a machine, test mode, and Start."""

    def __init__(self, executor: Executor, outlet: CloudOutlet, launched: Launched) -> None:
        """Send through `executor`; errors after a close go to `outlet`; a started run goes to `launched`."""
        super().__init__(executor)
        self._plans = LatestChannel(executor)
        self._launch = ExclusiveChannel(executor)
        self._outlet = outlet
        self._launched = launched
        self.phase: StartPhase = "closed"
        self.form = StartForm()
        self.error: ErrorView | None = None

    @property
    def can_start(self) -> bool:
        """Return whether Start applies: a plan with work is shown and nothing is starting."""
        view = self.form.view
        return self.phase == "ready" and view is not None and not view.empty

    def show_dialog(self) -> None:
        """Open the dialog fresh (no test mode, the default wait) and plan."""
        self.form = StartForm()
        self.error = None
        self.phase = "planning"
        self.plan_again()

    def close(self) -> None:
        """Close the dialog; a launch in flight still reaches the run list."""
        self.phase = "closed"
        self.changed()

    def plan_again(self) -> None:
        """Plan again while the dialog is open and not starting (test mode changed, a job deleted, Retry)."""
        if self.phase not in {"planning", "ready"}:
            return
        self.error = None
        self.phase = "planning"
        self._plans.send(PLAN_KEY, self.form.plan_request(), self._planned)
        self.changed()

    def use_test_mode(self, *, enabled: bool) -> None:
        """Switch test mode (a 2 h limit, the machine kept for SSH) and plan again, as the fingerprint changes."""
        if enabled != self.form.test_mode and self.phase != "launching":
            self.form.test_mode = enabled
            self.plan_again()

    def retry(self) -> None:
        """Try the step that failed again: Start when a plan is shown, else the plan."""
        if self.form.plan is None:
            self.plan_again()
            return
        self.error = None
        self.start()

    def start(self) -> None:
        """Start the cloud machine on the plan shown (one launch at a time)."""
        plan_shown = self.phase == "ready" and self.form.plan is not None
        if not plan_shown or self._launch.busy(LAUNCH_KEY):
            return
        # Before the send: an executor may answer inside it.
        self.phase = "launching"
        self.error = None
        self.changed()
        self._launch.send(LAUNCH_KEY, self.form.launch_request(), self._answered)

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
        self.changed()

    def _answered(self, response: Response) -> None:
        if isinstance(response, LaunchWorkerResponse):
            self.phase = "launched"
            self.changed()
            self._launched(response)
            return
        error = None
        if isinstance(response, ErrorResponse) and response.error.code != ERROR_CANCELLED:
            error = error_view(response)
        if self.phase == "closed":
            # Closed while starting: nothing started; an error still shows, in the window's dialog.
            if error is not None:
                self._outlet.show_error(error, None)
            return
        self.phase = "ready"
        self.error = error
        if isinstance(response, NeedsConfirmation):
            asked = asked_plan(response)
            if asked is None:
                self.error = internal_error(self.form.launch_request())
            else:
                self.form.show(*asked, changed=True)
        self.changed()
