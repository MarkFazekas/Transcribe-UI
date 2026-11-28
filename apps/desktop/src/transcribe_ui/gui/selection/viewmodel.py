"""The New job page's view-model: folder, tree, filters, selection and languages (architecture §3.1 "GUI").

Qt-free. It ties the parts together: the settings pre-fill the scan options and the language
choosers (`choices.py`), the chosen folder is scanned (`scanning.py`), each tick appends an
operation and previews the selection (`ticks.py`), the filters go into the folder ticks and
dim what they do not match (`filters.py`), the target languages (`targets.py`) and the save
(`saving.py`). "Edit selection" on a job page reopens a job: its stored composition (scan,
operations, languages) when it has one, else an empty selection on its scan root.
"""

from transcribe_ui.discovery.classes import SelectionFilter, SelectionOperation
from transcribe_ui.gui.executor import Executor
from transcribe_ui.gui.selection.choices import SettingsSource
from transcribe_ui.gui.selection.constants import UNCHECKED
from transcribe_ui.gui.selection.display import save_hint
from transcribe_ui.gui.selection.editing import JobLoader, Reopened
from transcribe_ui.gui.selection.filters import FilterInput, FilterModel
from transcribe_ui.gui.selection.saving import Draft, SaveCommands, SaveRequest, SelectionOutlet, save_request
from transcribe_ui.gui.selection.scanning import ScanModel
from transcribe_ui.gui.selection.targets import TargetModel
from transcribe_ui.gui.selection.ticks import TickModel
from transcribe_ui.gui.selection.tree import Scanned
from transcribe_ui.gui.viewmodel import ViewModel


class NewJobModel(ViewModel):
    """The New job page (step 1 of a job): a new job, or an existing job's selection reopened."""

    def __init__(self, executor: Executor, outlet: SelectionOutlet) -> None:
        """Send through `executor`; signals, questions and errors go to `outlet`."""
        super().__init__(executor)
        self.settings = SettingsSource(executor, self.changed)
        self.scan = ScanModel(executor, self.changed, self._scanned)
        self.ticks = TickModel(executor, self.changed)
        self.filters = FilterModel(executor, self.changed)
        self.targets = TargetModel(self.changed)
        self._loader = JobLoader(executor, outlet, self.settings.spec, self._reopen)
        self.saver = SaveCommands(executor, outlet, self.start_new, self._loader.forget)

    def choose_folder(self, root: str) -> None:
        """Scan a folder with the settings' options.

        Another folder than the one shown, or any folder while a job is edited, starts a new job
        with an empty selection (the job's languages kept); the same folder keeps the selection.
        """
        scanned = self.scan.scanned
        editing = self.saver.editing is not None
        reset = editing or scanned is None or scanned.index.tree.root != root
        self.saver.leave(selection_dropped=reset)
        if reset:
            self.ticks.start(None, ())
            self.targets.reset(self.targets.languages)
        self.scan.start(self.settings.spec(root))

    def tick(self, path: str, *, folder: bool) -> None:
        """Tick or untick a row: include it, or exclude it when it is selected (`includes`).

        A folder's operation carries the active filters, a file's none.
        """
        if self.scan.scanned is None:
            return
        chosen = self.filters.current() if folder else SelectionFilter()
        if chosen is None:
            return
        include = includes(self, path, folder=folder)
        operation = SelectionOperation(operation="include" if include else "exclude", path=path, filters=chosen)
        self.ticks.add(operation)

    def change_filters(self, picked: FilterInput) -> None:
        """Use these filters for the next folder ticks and show what they match."""
        self.filters.change(picked, self.scan.scanned)

    def save_request(self) -> SaveRequest | None:
        """Return the `create_batch` (or `edit_batch`) the page would send now, or None while it cannot be saved."""
        scanned = self.scan.scanned
        if scanned is None or save_hint(self) is not None:
            return None
        entries = self.targets.entries(self.ticks.marks.files)
        draft = Draft(scanned, self.ticks.operations, self.targets.languages, entries)
        return save_request(draft, self.saver.editing)

    def save(self) -> None:
        """Create the job (or save the edited job's selection); nothing while it cannot be saved."""
        request = self.save_request()
        if request is not None:
            self.saver.send(request)

    def start_new(self) -> None:
        """Start a new job on the folder shown: an empty selection, the job's languages kept."""
        self.saver.leave()
        self.ticks.start(self.scan.scanned, ())
        self.targets.reset(self.targets.languages)

    def edit_batch(self, batch_id: str) -> None:
        """Reopen a job's selection ("Edit selection"): load the job, then scan its folder."""
        self._loader.load(batch_id)

    def _reopen(self, selection: Reopened) -> None:
        self.saver.leave()
        self.saver.editing = selection.target
        self.ticks.start(None, selection.operations)
        self.targets.reset(selection.languages, selection.own)
        self.scan.start(selection.spec)

    def _scanned(self, scanned: Scanned) -> None:
        self.ticks.start(scanned, self.ticks.operations)
        self.filters.refresh(scanned)


def _matching_selected(model: NewJobModel, path: str) -> bool | None:
    """Return whether every file below a folder the active filters match is selected (None: unknown)."""
    scanned = model.scan.scanned
    if scanned is None:
        return None
    index = scanned.index
    matches = model.filters.current_matches(index.tree.scan_id)
    if matches is None:
        return None
    node = index.nodes.get(path)
    if node is None:
        return None
    below = {found.path for found in index.files_below(node)}
    matching = below & matches.files
    return bool(matching) and matching <= model.ticks.marks.files


def includes(model: NewJobModel, path: str, *, folder: bool) -> bool:
    """Return whether a click on a row includes it (else it excludes it).

    A click still waiting for its answer decides by what it asked for, so a fast second click
    toggles. Under active filters a folder click decides by the files below it the filters
    match: exclude when all of them are selected, else include (an exclude of a folder's other
    files would change nothing). Otherwise a row with nothing selected is included, any other
    (ticked or partly) excluded.
    """
    pending = model.ticks.pending.get(path)
    if folder and pending is None:
        selected = _matching_selected(model, path)
        if selected is not None:
            return not selected
    return model.ticks.mark(path, folder=folder) == UNCHECKED
