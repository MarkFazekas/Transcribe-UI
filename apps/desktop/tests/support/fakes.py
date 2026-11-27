"""Deterministic port fakes."""

import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from support.credentials import memory_store
from support.runpod_api import FakeRunpodApi
from transcribe_ui.api.ports import Ports, UlidSource
from transcribe_ui.media.fake import FakeMediaTools
from transcribe_ui.runpod.access import RunpodAccess
from transcribe_ui.runpod.timing import Stopped
from transcribe_ui.state.paths import AppPaths
from transcribe_ui.state.store import LocalState
from transcribe_ui.storage.client import r2_client

# The fixed "now" of every test backend.
FIXED_NOW = datetime.fromisoformat("2026-11-27T12:00:00+00:00")
# Seed of the id source; both parity legs use it, so they create the same ids.
ID_SEED = 20261127
# Folder under a test root holding the app's data, config, cache and log dirs.
APP_STATE_FOLDER = "app"
# Runs lock wait of test backends: long enough for a free lock, short enough for `runs_busy` tests.
TEST_RUNS_LOCK_WAIT_S = 0.5


@dataclass(frozen=True)
class FixedClock:
    """A clock that never moves."""

    moment: datetime = FIXED_NOW

    def now(self) -> datetime:
        """Return the fixed moment."""
        return self.moment


def app_paths_under(root: Path) -> AppPaths:
    """Return the app directories of a test root (`<root>/app/{data,config,cache,log}`)."""
    return AppPaths.under(root / APP_STATE_FOLDER)


def seeded_ports(root: Path, seed: int = ID_SEED) -> Ports:
    """Return seeded test ports with fakes and fixed values.

    Fixed clock, seeded ULIDs, fake media tools, an empty in-memory keyring and a fake Runpod
    API (never the real one). The local state lives under `root`.

    Close `ports.state` when done (it may hold the database and its writer lock).
    """
    clock = FixedClock()
    generator = random.Random(seed)  # noqa: S311 -- reproducible test ids, not cryptography.
    state = LocalState(app_paths_under(root), runs_lock_wait_s=TEST_RUNS_LOCK_WAIT_S)
    return Ports(
        clock=clock,
        ids=UlidSource(clock, generator.getrandbits),
        media=FakeMediaTools(),
        state=state,
        credentials=memory_store(),
        r2_clients=r2_client,
        # Never the real Runpod API: a fake account that accepts the test key.
        runpod=RunpodAccess(transport=FakeRunpodApi(clock.now).transport(), wait=skip_wait),
    )


def skip_wait(_seconds: float, _stopped: Stopped) -> None:
    """Return at once instead of waiting between attempts."""
