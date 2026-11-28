"""The review page's view-model: step 2 of a job, review and upload (architecture §3.1 "GUI" "Review and upload").

Qt-free. It ties the parts together: the job and its review views (`loading.py`), the choices
and ticks the user makes (`editing.py`), Apply and Remove from job (`commands.py`), all sent one
at a time (`sender.py`, since each holds the job's lock), and the job's upload in the activity
panel (`uploads.py`). The page works only while it is shown: a hidden page cancels its review and
sends nothing; shown again, it loads once if something asked for a load meanwhile (it is stale),
so a change of the job elsewhere costs nothing meanwhile. Choices change only while nothing holds
the job (`editable`); a review still queued behind other long calls does not hold it.
"""

from transcribe_ui.batches.classes import ReviewFilter
from transcribe_ui.gui.constants import BATCHES_CHANGED_SIGNAL
from transcribe_ui.gui.executor import Executor
from transcribe_ui.gui.review.commands import ReviewCommands, ReviewOutlet
from transcribe_ui.gui.review.constants import UPLOAD_ACTION
from transcribe_ui.gui.review.editing import ChoiceEditor
from transcribe_ui.gui.review.loading import ReviewLoader
from transcribe_ui.gui.review.sender import SerialSender
from transcribe_ui.gui.review.uploads import UploadTracker
from transcribe_ui.gui.viewmodel import ViewModel
from transcribe_ui.i18n.catalog import tr


class ReviewModel(ViewModel):
    """The review and upload step of one job."""

    def __init__(self, executor: Executor, outlet: ReviewOutlet, uploads: UploadTracker) -> None:
        """Send through `executor`; signals, questions and errors go to `outlet`; uploads run in `uploads`."""
        super().__init__(executor)
        self.outlet = outlet
        self.uploads = uploads
        self.sender = SerialSender(executor)
        self.loader = ReviewLoader(executor, self.sender, self.changed, uploads.busy)
        self.loader.on_settled = self._settled
        self.editor = ChoiceEditor(self.loader, self.editable, self.changed)
        settled = self._settled
        self.commands = ReviewCommands(self.sender, self.loader, self.editor, outlet, settled)
        # The page is telling the other pages that its job changed (its own signal is not a reload).
        self._telling = False
        uploads.subscribe(self._upload_changed)

    def open_batch(self, batch_id: str) -> None:
        """Show a job's review (another job starts without choices, ticks or filters) and load it."""
        if self.loader.open_batch(batch_id):
            self.editor.reset()
        self.loader.page.stale = True
        self._load_if_shown()
        self.changed()

    def visibility_changed(self, *, shown: bool) -> None:
        """Note whether the page is visible: hidden, it stops its review and sends nothing; shown again, it loads.

        Leaving with choices not applied says so in the status bar (the job page's Upload ignores them).
        """
        page = self.loader.page
        page.shown = shown
        if shown:
            self._load_if_shown()
            return
        if self.sender.reviewing():
            self.sender.drop_reviews()
            page.stale = True
        if self.editor.draft.keys():
            self.outlet.show_status(tr("Your review choices are not applied yet; open Review to apply them."))

    def on_batches_changed(self, batch_ids: object) -> None:
        """Load the job again when another page changed it (an empty tuple: any job)."""
        batch_id = self.loader.batch_id
        named = isinstance(batch_ids, tuple) and (not batch_ids or batch_id in batch_ids)
        if self._telling or batch_id is None or not named:
            return
        self.loader.page.stale = True
        self._load_if_shown()

    def filter_by(self, languages: tuple[str, ...], folder: str | None) -> None:
        """Narrow the views to these languages (all when empty) and this folder (None: all), and load them.

        Refused while choices wait for Apply: a choice is only kept for what the review shows.
        """
        filters = ReviewFilter(languages=languages, folder=folder)
        if filters != self.loader.filters and not self.editor.draft.keys():
            self.loader.filters = filters
            self.loader.generation += 1
            self.loader.load_review()

    def upload(self) -> bool:
        """Start (or resume) the job's upload in the activity panel; False when the job does not allow it now."""
        batch_id = self.loader.batch_id
        detail = self.loader.loaded.detail
        allowed = detail is not None and UPLOAD_ACTION in detail.summary.actions
        free = self.editable() and not self.editor.draft.keys()
        if batch_id is None or not allowed or not free:
            return False
        return self.uploads.start(batch_id)

    def editable(self) -> bool:
        """Return whether nothing holds the job now (a check, a save, its upload), so what is shown can change."""
        batch_id = self.loader.batch_id
        upload = batch_id is not None and self.uploads.busy(batch_id)
        checking = self.sender.reviewing(started_only=True)
        return not (upload or checking or self.sender.acting())

    def _load_if_shown(self) -> None:
        page = self.loader.page
        if page.shown and page.stale and self.loader.batch_id is not None:
            page.stale = False
            self.loader.reload()

    def _settled(self, batch_id: str, *, tell: bool = True) -> None:
        """Drop the choices and ticks the latest review no longer lists; `tell` the other pages the job changed."""
        self.editor.prune()
        if not tell:
            self.changed()
            return
        self._telling = True
        self.outlet.emit(BATCHES_CHANGED_SIGNAL, (batch_id,))
        self._telling = False

    def _upload_changed(self, batch_id: str) -> None:
        # An upload that ends emits `batches_changed`, which loads the job again.
        if batch_id == self.loader.batch_id:
            self.changed()
