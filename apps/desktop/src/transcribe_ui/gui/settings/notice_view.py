"""The window's start-up notice (accounts, cloud setup, a failed cloud check) and the restart question (Q32)."""

from collections.abc import Callable, Mapping

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.settings.account_texts import notice_text
from transcribe_ui.gui.settings.constants import NoticeKind
from transcribe_ui.gui.settings.startup import FirstStart
from transcribe_ui.gui.settings.texts import restart_question
from transcribe_ui.gui.settings.widgets import show_text
from transcribe_ui.gui.texts import fix_label
from transcribe_ui.i18n.catalog import tr

# What a notice's button does.
type NoticeAction = Callable[[], object]
# What each notice's button does, by notice.
type NoticeActions = Mapping[NoticeKind, NoticeAction]


class NoticeBar(QFrame):
    """A line above the pages with what is missing and a button that fixes it; Hide hides it for the session."""

    def __init__(
        self, model: FirstStart, actions: NoticeActions, error_text: Callable[[], str], parent: QWidget
    ) -> None:
        """Follow `model`; the button runs the notice's action; `error_text` is the failed cloud check's text."""
        super().__init__(parent)
        self._model = model
        self._actions = actions
        self._error_text = error_text
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.label = wrapped_label("", self)
        self.action_button = QPushButton(self)
        self.action_button.clicked.connect(self._act)
        self._lay_out()
        model.subscribe(self._repaint)
        self._repaint()

    def _lay_out(self) -> None:
        hide_button = QPushButton(tr("Hide"), self)
        hide_button.clicked.connect(self._model.hide)
        layout = QHBoxLayout(self)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.action_button)
        layout.addWidget(hide_button)

    def _act(self) -> None:
        notice = self._model.notice
        if notice is not None:
            self._actions[notice]()

    def _repaint(self) -> None:
        notice = self._model.notice
        self.setVisible(notice is not None)
        if notice is None:
            return
        show_text(self.label, notice_text(notice, self._error_text()))
        self.action_button.setText(fix_label("retry" if notice == "cloud_error" else notice))


class RestartQuestion(QDialog):
    """Q32: "Restart now to change the language?" with Restart now and Later."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Ask; Restart now accepts, Later rejects."""
        super().__init__(parent)
        self.setWindowTitle(tr("Language"))
        layout = QVBoxLayout(self)
        layout.addWidget(wrapped_label(restart_question(), self))
        buttons = QDialogButtonBox(self)
        self.restart_button = buttons.addButton(tr("Restart now"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(tr("Later"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
