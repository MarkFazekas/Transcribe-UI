"""The cross-area contract of the GUI: navigation and action signals, change signals, start-up hooks.

Declared once, before any area exists (plan m6 §1 "Cross-area contract"), so later parts connect
to it instead of editing each other's packages. An area emits a signal; the area that handles
it connects its handler in `gui/shell.py` (one line each). Until a handler is connected, the
shell answers an action signal with "Not available yet".
"""

from collections.abc import Callable
from enum import IntEnum
from operator import itemgetter

from PySide6.QtCore import QObject, Signal


class GuiSignals(QObject):
    """The bus every area emits on and connects to; lives on the GUI thread."""

    # Navigation.
    # Open the job page of a batch (`batch_id`).
    open_job = Signal(str)
    # Open the jobs list.
    open_jobs = Signal()
    # Open the New job page with an empty selection.
    open_new_job = Signal()
    # Open the Cloud page.
    open_cloud = Signal()
    # Open the Cloud page's list of cloud machines (stragglers, overdue pods).
    open_cloud_machines = Signal()
    # Open a Settings section (`SECTION_*` of `gui/constants.py`; "" for the first one).
    open_settings = Signal(str)

    # Job actions, emitted by the job page (M6.5) and handled by the named area.
    # Reopen a batch's selection in New job (`batch_id`; `selection/`).
    edit_selection = Signal(str)
    # Open a batch's review step (`batch_id`; `review/`).
    open_review = Signal(str)
    # Start or resume a batch's upload (`batch_id`; `review/`).
    run_upload = Signal(str)
    # Open a batch's download dialog (`batch_id`; `download/`).
    open_download = Signal(str)
    # Retry jobs of a batch (`batch_id`, the ticked jobs as a tuple of `RetryJob`; `cleanup/`).
    retry_jobs = Signal(str, object)
    # Clean up a batch's cloud data (`batch_id`, the cleanup scope; `cleanup/`).
    clean_up = Signal(str, str)

    # Cloud actions, emitted by the start dialog (M6.6).
    # Delete an unknown batch's cloud data (`batch_id`; `cleanup/`, Q28).
    delete_unknown_batch = Signal(str)
    # Back to the start dialog after such a delete: plan the launch again.
    plan_invalidated = Signal()

    # Changes: reload what shows them.
    # Batches changed (a tuple of batch ids; empty: any batch).
    batches_changed = Signal(object)
    # Runs changed (launched, refreshed, ended).
    runs_changed = Signal()
    # The settings were saved.
    settings_changed = Signal()


# The action signals an area handles, by name; the shell shows "Not available yet" for each until connected.
ACTION_SIGNALS = (
    "edit_selection",
    "open_review",
    "run_upload",
    "open_download",
    "retry_jobs",
    "clean_up",
    "delete_unknown_batch",
)
# The signals that take the user to another page, job or step, by name; an answer that arrives
# after one must not pull the user back (the cleanup handlers count them). A new navigation signal goes here.
NAVIGATION_SIGNALS = (
    "open_job",
    "open_jobs",
    "open_new_job",
    "open_cloud",
    "open_cloud_machines",
    "open_settings",
    "edit_selection",
    "open_review",
    "open_download",
)


class StartupSlot(IntEnum):
    """Where a start-up step runs in the sequence (plan m6 §1 "Start-up sequence")."""

    # `get_settings` (M6.3).
    SETTINGS = 10
    # `credentials_status` (M6.3).
    CREDENTIALS = 20
    # The writer-lock probe (M6.2): the first database request takes the lock or shows the notice.
    DATABASE = 30
    # `list_batches` (M6.5).
    BATCHES = 40
    # `list_runs` (M6.6).
    RUNS = 50
    # `reconcile_cloud`, only when a Runpod key is set (M6.6).
    RECONCILE = 60
    # `refresh_status`, only when a Runpod key is set (M6.6).
    REFRESH = 70


# A start-up step: gets `proceed`, which it calls exactly once when it is done (whatever the outcome).
type StartupStep = Callable[[Callable[[], None]], None]
# Called after the last start-up step proceeded (None: nobody).
type Finished = Callable[[], None] | None


class StartupHooks:
    """The ordered start-up steps that areas register into; `app.py` runs them after the window shows.

    Qt-free. Steps run one after the other: by slot, then in registration order. A step sends
    its request and calls `proceed` from the request's callback, so the sequence runs in the
    background without blocking the GUI thread.
    """

    def __init__(self) -> None:
        """Start with no steps."""
        self._steps: list[tuple[StartupSlot, int, StartupStep]] = []

    def register(self, slot: StartupSlot, step: StartupStep) -> None:
        """Run `step` in `slot` (after the steps registered earlier in the same slot)."""
        self._steps.append((slot, len(self._steps), step))

    def run(self, on_finished: Callable[[], None] | None = None) -> None:
        """Run every step in order; `on_finished` is called after the last one proceeded."""
        ordered = sorted(self._steps, key=itemgetter(0, 1))
        _Proceed([step for _slot, _order, step in ordered], 0, on_finished)()


class _Proceed:
    """The `proceed` a step gets: runs the step after it, once (a second call does nothing)."""

    def __init__(self, steps: list[StartupStep], index: int, on_finished: Finished) -> None:
        self._steps = steps
        self._index = index
        self._on_finished = on_finished
        self._called = False

    def __call__(self) -> None:
        if self._called:
            return
        self._called = True
        if self._index == len(self._steps):
            if self._on_finished is not None:
                self._on_finished()
            return
        follower = _Proceed(self._steps, self._index + 1, self._on_finished)
        self._steps[self._index](follower)
