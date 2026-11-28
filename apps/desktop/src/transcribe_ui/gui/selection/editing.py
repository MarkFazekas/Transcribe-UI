"""Reopening a job's selection ("Edit selection" on a job page; architecture §3.1 "GUI").

Qt-free. The job is loaded with `get_batch`; its stored composition (the scan with its
options, the operations, the languages and each file's own languages) is what the page starts
from. A job without one (created before the app kept it, or a retry job) starts from an empty
selection on its scan root, with the settings' scan options and the job's languages.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from transcribe_ui.api.classes import ErrorResponse, Response
from transcribe_ui.batches.classes import GetBatchRequest, ItemLanguages
from transcribe_ui.batches.responses import BatchDetail, GetBatchResponse
from transcribe_ui.discovery.classes import ScanSpec, SelectionOperation
from transcribe_ui.gui.executor import Executor, LatestChannel
from transcribe_ui.gui.feedback import error_view
from transcribe_ui.gui.selection.constants import EDIT_KEY, Languages
from transcribe_ui.gui.selection.saving import EditTarget, SelectionOutlet

# Returns the scan of a folder with the settings' options.
type SpecFor = Callable[[str], ScanSpec]


@dataclass(frozen=True)
class Reopened:
    """What the page starts from when it edits a job's selection."""

    target: EditTarget
    spec: ScanSpec
    operations: tuple[SelectionOperation, ...]
    languages: Languages
    own: tuple[ItemLanguages, ...]


def reopened(detail: BatchDetail, spec_for: SpecFor) -> Reopened:
    """Return the selection a job reopens: its stored composition, else an empty one on its scan root."""
    summary = detail.summary
    target = EditTarget(summary.batch_id, summary.stage)
    stored = detail.composition
    if stored is None:
        return Reopened(target, spec_for(detail.scan_root), (), summary.languages, ())
    return Reopened(target, stored.scan, stored.operations, stored.languages, stored.item_languages)


class JobLoader:
    """Loads the job whose selection is edited (a newer request replaces one in flight)."""

    def __init__(
        self,
        executor: Executor,
        outlet: SelectionOutlet,
        spec_for: SpecFor,
        on_loaded: Callable[[Reopened], None],
    ) -> None:
        """Load through `executor`; an error goes to `outlet`; `on_loaded` gets the reopened selection."""
        self._channel = LatestChannel(executor)
        self._outlet = outlet
        self._spec_for = spec_for
        self._on_loaded = on_loaded
        # Numbers the loads: the answer of a load forgotten or replaced does not count.
        self._generation = 0

    def load(self, batch_id: str) -> None:
        """Load a job's selection."""
        self._generation += 1
        request = GetBatchRequest(action="get_batch", batch_id=batch_id)
        loaded = partial(self._loaded, self._generation, batch_id)
        self._channel.send(EDIT_KEY, request, loaded)

    def forget(self) -> None:
        """Drop the load in flight (the user started a new job meanwhile)."""
        self._generation += 1

    def _loaded(self, generation: int, batch_id: str, response: Response) -> None:
        if generation != self._generation:
            return
        if isinstance(response, GetBatchResponse):
            self._on_loaded(reopened(response.batch, self._spec_for))
        elif isinstance(response, ErrorResponse):
            self._outlet.show_error(error_view(response), partial(self.load, batch_id))
