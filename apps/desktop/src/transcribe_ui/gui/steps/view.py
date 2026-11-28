"""The step header widget: the four steps of a job, with Back and Next (plan m6 §1 "Layout").

The pages of the steps (New job, review and upload, start transcription, download) place it at
their top and give it the job's stage; Back and Next show only when the page connects them.
It decides nothing: the stage is the backend's, the navigation the page's.
"""

from collections.abc import Callable

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from transcribe_ui.batches.responses import BatchStage
from transcribe_ui.gui.steps.constants import DONE_MARK, STEP_SEPARATOR, STEPS
from transcribe_ui.gui.steps.viewmodel import StepView, step_views
from transcribe_ui.i18n.catalog import tr

# What Back or Next does (None: the button is hidden).
type Navigate = Callable[[], object] | None


def step_text(step: StepView) -> str:
    """Return a step's label: its number and title, a check mark when done."""
    text = f"{step.number}. {step.title}"
    return f"{DONE_MARK} {text}" if step.state == "done" else text


class StepHeader(QWidget):
    """One label per step (the current one bold, the shown one underlined), Back and Next."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Start at the first step, without Back and Next."""
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.back_button = self._button(tr("Back"), self._go_back)
        layout.addWidget(self.back_button)
        self.step_labels = self._step_labels(layout)
        layout.addStretch()
        self.next_button = self._button(tr("Next"), self._go_next)
        layout.addWidget(self.next_button)
        self._back: Navigate = None
        self._next: Navigate = None
        self.steps: tuple[StepView, ...] = ()
        self.show_stage(None)
        self.set_navigation(None, None)

    def show_stage(self, stage: BatchStage | None, shown: str | None = None) -> None:
        """Show a job at `stage` (None: a job not created yet), the page showing step `shown` (default: the stage's)."""
        self.steps = step_views(stage, shown)
        for label, step in zip(self.step_labels, self.steps, strict=True):
            label.setText(step_text(step))
            font = label.font()
            font.setBold(step.state == "current")
            font.setUnderline(step.shown)
            label.setFont(font)

    def set_navigation(self, back: Navigate, forward: Navigate) -> None:
        """Show Back and Next with what they do; None hides the button."""
        self._back = back
        self._next = forward
        self.back_button.setVisible(back is not None)
        self.next_button.setVisible(forward is not None)

    def _button(self, text: str, on_click: Callable[[], None]) -> QPushButton:
        button = QPushButton(text, self)
        button.clicked.connect(on_click)
        return button

    def _step_labels(self, layout: QHBoxLayout) -> list[QLabel]:
        labels: list[QLabel] = []
        for index, _key in enumerate(STEPS):
            if index:
                layout.addWidget(QLabel(STEP_SEPARATOR, self))
            label = QLabel(self)
            labels.append(label)
            layout.addWidget(label)
        return labels

    def _go_back(self) -> None:
        if self._back is not None:
            self._back()

    def _go_next(self) -> None:
        if self._next is not None:
            self._next()
