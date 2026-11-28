"""The end-to-end flow through the GUI, compared with the CLI flow (pytest-qt, offscreen; plan m6 M6.11).

The real window runs over a leg built like the CLI flow's (`test_end_to_end.py`: the seeded
factory, the `prepare` tree, fake media, moto, the fake Runpod API), through `QtExecutor`, and a
user goes through the flow (`support/flow_steps.py`). Every request that reaches the backend is
recorded. The CLI flow's steps must come in order; between them only requests that change nothing
are allowed. The matched requests, replayed through the parity runner on fresh legs, must give the
CLI flow's goldens byte for byte, and both flows must draw the same ids and send each step at the
same clock.
"""

import time
from collections.abc import Iterator
from contextlib import closing
from functools import partial
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from support.cloud import ManualTimers
from support.credentials import moto_bucket
from support.end_to_end import STEPS, golden_bytes, golden_name
from support.flow_steps import GuiFlow, drive
from support.gui import untracked_window
from support.gui_flow import FlowRecorder, assert_same_clock, assert_same_draws, match_steps, replay, request_document
from support.launch_legs import LaunchLegs
from support.selection_window import settled
from support.upload import build_upload_tree
from transcribe_ui.api.backend import Backend
from transcribe_ui.gui import areas
from transcribe_ui.gui.cloud.install import install_cloud

# Prefix of the GUI leg's bucket, apart from the replay's legs in the same moto mock.
GUI_BUCKET_PREFIX = "transcribe-gui-"
# Most seconds the GUI flow may take (generous: a regression guard, not a benchmark).
FLOW_BUDGET_S = 60.0


@pytest.fixture
def flow(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[GuiFlow]:
    """Return the shown window over a leg built like the CLI flow's; every call has answered afterwards.

    The Cloud area gets manual timers through its install function's `timers`, so the poll's
    interval passes only when the flow says so.
    """
    timers = ManualTimers()
    monkeypatch.setattr(areas, "install_cloud", partial(install_cloud, timers=timers))
    root = tmp_path / "gui"
    root.mkdir()
    build_upload_tree(root)
    with moto_bucket():
        legs = LaunchLegs(bucket_prefix=GUI_BUCKET_PREFIX)
        ports = legs(root)
        recorder = FlowRecorder(ports.clock)
        recorder.attach(Backend(recorder.wrap(ports)))
        with closing(ports.state):
            window = untracked_window(recorder, tmp_path / "log")
            qtbot.addWidget(window, before_close_func=partial(settled, qtbot))
            window.show()
            yield GuiFlow(window, recorder, legs, root, timers)
            settled(qtbot, window)


def test_the_gui_sends_the_cli_flow(
    qtbot: QtBot, flow: GuiFlow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The GUI sends the CLI flow's steps in order and nothing else that changes something; replayed, they give its goldens."""
    began = time.monotonic()
    drive(qtbot, flow, monkeypatch)
    elapsed = time.monotonic() - began
    sent = flow.recorder.answered()
    matched = match_steps(sent)
    assert not flow.recorder.stray_draws
    replayed = replay(tmp_path / "replay", [request_document(each.request, flow.root) for each in matched])
    assert_same_draws(matched, sent, replayed)
    assert_same_clock(matched, replayed)
    assert len(replayed.exchanges) == len(STEPS)
    for index, (each, exchange) in enumerate(zip(matched, replayed.exchanges, strict=True)):
        replied = exchange.golden().encode("utf-8")
        assert replied == golden_bytes(index), f"{each.name()}: its replay differs from {golden_name(index)}"
    assert elapsed < FLOW_BUDGET_S, f"the GUI flow took {elapsed:.1f} s"
