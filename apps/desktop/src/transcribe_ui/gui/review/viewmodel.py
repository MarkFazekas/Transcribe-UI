"""The review page's view-model: step 2 of a job, review and upload (architecture §3.1 "GUI" "Review and upload").

Qt-free. It ties the parts together: the job and its review views (`loading.py`), the choices
and ticks the user makes (`editing.py`), Apply and Remove from job (`commands.py`), all sent one
at a time (`sender.py`, since each holds the job's lock), and the job's upload in the activity
panel (`uploads.py`). The page works only while it is shown: a hidden page cancels its review
and loads again when it is shown, so a change of the job elsewhere costs nothing meanwhile.
"""

from transcribe_ui.batches.classes import ReviewFilter
from transcribe_ui.gui.executor import Executor
from transcribe_ui.gui.review.commands import ReviewCommands, ReviewOutlet
from transcribe_ui.gui.review.constants import BATCHES_CHANGED_SIGNAL
from transcribe_ui.gui.review.editing import ChoiceEditor
from transcribe_ui.gui.review.loading import ReviewLoader
from transcribe_ui.gui.review.sender import SerialSender
from transcribe_ui.gui.review.uploads import UploadTracker
from transcribe_ui.gui.viewmodel import ViewModel


class ReviewModel(ViewModel):
    """The review and upload step of one job."""

    def __init__(self, executor: Executor, outlet: ReviewOutlet, uploads: UploadTracker) -> None:
        """Send through `executor`; signals, questions and errors go to `outlet`; uploads run in `uploads`."""
        super().__init__(executor)
        self.outlet = outlet
        self.uploads = uploads
        self.sender = SerialSender(executor)
        self.loader = ReviewLoader(executor, self.sender, self.changed, uploads.busy)
        self.loader.on_reviewed = self._tell_changed
        self.editor = ChoiceEditor(self.loader, self.changed)
        tell = self._tell_changed
        self.commands = ReviewCommands(self.sender, self.loader, self.editor, outlet, tell)
        self._shown = False
        # The job changed while the page was hidden: load it again once shown.
        self._stale = False
        # The page is telling the other pages that its job changed (its own signal is not a reload).
        self._telling = False
        uploads.subscribe(self._upload_changed)

    def open_batch(self, batch_id: str) -> None:
        """Show a job's review (another job starts without choices, ticks or filters) and load it."""
        if self.loader.open_batch(batch_id):
            self.editor.reset()
        self._stale = True
        self._load_if_shown()
        self.changed()

    def visibility_changed(self, *, shown: bool) -> None:
        """Note whether the page is visible: hidden, its review stops; shown again, it loads."""
        self._shown = shown
        if shown:
            self._load_if_shown()
        elif self.sender.reviewing():
            self.sender.drop_reviews()
            self._stale = True

    def on_batches_changed(self, batch_ids: object) -> None:
        """Load the job again when another page changed it (an empty tuple: any job)."""
        batch_id = self.loader.batch_id
        named = isinstance(batch_ids, tuple) and (not batch_ids or batch_id in batch_ids)
        if self._telling or batch_id is None or not named:
            return
        self._stale = True
        self._load_if_shown()

    def filter_by(self, languages: tuple[str, ...], folder: str | None) -> None:
        """Narrow the views to these languages (all when empty) and this folder (None: all), and load them."""
        filters = ReviewFilter(languages=languages, folder=folder)
        if filters != self.loader.filters:
            self.loader.filters = filters
            self.loader.generation += 1
            self.loader.load_review()

    def upload(self) -> None:
        """Start (or resume) the job's upload in the activity panel."""
        batch_id = self.loader.batch_id
        if batch_id is not None:
            self.uploads.start(batch_id)

    def _load_if_shown(self) -> None:
        if self._shown and self._stale and self.loader.batch_id is not None:
            self._stale = False
            self.loader.reload()

    def _tell_changed(self, batch_id: str) -> None:
        self._telling = True
        self.outlet.emit(BATCHES_CHANGED_SIGNAL, (batch_id,))
        self._telling = False

    def _upload_changed(self, batch_id: str) -> None:
        # An upload that ends emits `batches_changed`, which loads the job again.
        if batch_id == self.loader.batch_id:
            self.changed()
