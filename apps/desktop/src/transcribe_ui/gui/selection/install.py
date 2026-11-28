"""Puts the New job page into the window: registration, "Edit selection", New job, the settings loads."""

from functools import partial
from typing import TYPE_CHECKING

from transcribe_ui.gui.constants import PAGE_NEW_JOB
from transcribe_ui.gui.jobs.install import WindowOutlet
from transcribe_ui.gui.selection.view import NewJobPage
from transcribe_ui.gui.selection.viewmodel import NewJobModel
from transcribe_ui.gui.signals import StartupSlot
from transcribe_ui.gui.texts import page_title

if TYPE_CHECKING:
    from transcribe_ui.gui.shell import MainWindow


def _edit_selection(window: "MainWindow", model: NewJobModel, batch_id: str) -> None:
    window.pages.show_page(PAGE_NEW_JOB)
    model.edit_batch(batch_id)


def install_selection(window: "MainWindow") -> NewJobPage:
    """Register the New job page, handle `edit_selection` and `open_new_job`, and load the settings it uses."""
    context = window.context
    model = NewJobModel(context.executor, WindowOutlet(window))
    page = NewJobPage(model, window)
    window.pages.add_page(PAGE_NEW_JOB, page_title(PAGE_NEW_JOB), page, needs_database=True)
    window.connect_handler("edit_selection", partial(_edit_selection, window, model))
    signals = context.signals
    signals.open_new_job.connect(model.start_new)
    signals.settings_changed.connect(model.settings.load)
    context.hooks.register(StartupSlot.SETTINGS, model.settings.load)
    return page
