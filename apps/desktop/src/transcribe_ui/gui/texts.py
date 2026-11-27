"""Texts of the window frame that several GUI modules show (architecture §3.1 "Language of the texts").

Qt-free. Each is translated when it is shown (`tr`), never at import time, so the language the
GUI activates at start-up applies to all of them.
"""

from transcribe_ui.gui.constants import PAGE_CLOUD, PAGE_JOBS, PAGE_NEW_JOB, PAGE_SETTINGS, ActivityState, FixKind
from transcribe_ui.i18n.catalog import tr


def page_title(page: str) -> str:
    """Return the navigation title of a page (by its key)."""
    titles = {
        PAGE_NEW_JOB: tr("New job"),
        PAGE_JOBS: tr("Jobs"),
        PAGE_CLOUD: tr("Cloud"),
        PAGE_SETTINGS: tr("Settings"),
    }
    return titles[page]


def fix_label(kind: FixKind) -> str:
    """Return the label of the button that fixes an error."""
    labels: dict[FixKind, str] = {
        "accounts": tr("Open accounts"),
        "cloud_setup": tr("Open cloud setup"),
        "open_batch": tr("Open the job"),
        "retry": tr("Retry"),
    }
    return labels[kind]


def activity_state_text(state: ActivityState) -> str:
    """Return the status text of an activity (a running one shows its progress, a failed one its error instead)."""
    texts: dict[ActivityState, str] = {
        "queued": tr("Waiting to start"),
        "running": tr("Running"),
        # Shown neutral, never as a failure.
        "stopped": tr("Stopped; continue any time."),
        "failed": tr("Failed"),
        "finished": tr("Finished"),
    }
    return texts[state]
