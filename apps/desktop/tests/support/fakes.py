"""Deterministic port fakes."""

import random
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from pathlib import Path

from support.credentials import memory_store
from support.runpod_api import FakeRunpodApi
from transcribe_ui.api.ports import Ports, UlidSource
from transcribe_ui.media.fake import FakeMediaTools
from transcribe_ui.media.ports import MediaTools
from transcribe_ui.runpod.access import RunpodAccess
from transcribe_ui.runpod.timing import Stopped
from transcribe_ui.state.installation import InstallationIdFile
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


def seeded_ports(
    root: Path,
    seed: int = ID_SEED,
    *,
    media: MediaTools | None = None,
    local_zone: tzinfo = UTC,
) -> Ports:
    """Return seeded test ports with fakes and fixed values.

    Fixed clock, seeded ULIDs, fake media tools (or `media`), an empty in-memory keyring and a
    fake Runpod API (never the real one); local times (batch ids) are in `local_zone` (UTC
    unless given). The local state lives under `root`.

    Close `ports.state` when done (it may hold the database and its writer lock).
    """
    clock = FixedClock()
    generator = random.Random(seed)  # noqa: S311 -- reproducible test ids, not cryptography.
    state = LocalState(app_paths_under(root), runs_lock_wait_s=TEST_RUNS_LOCK_WAIT_S)
    return Ports(
        clock=clock,
        ids=UlidSource(clock, generator.getrandbits),
        media=FakeMediaTools() if media is None else media,
        state=state,
        credentials=memory_store(),
        r2_clients=r2_client,
        # Never the real Runpod API: a fake account that accepts the test key.
        runpod=RunpodAccess(transport=FakeRunpodApi(clock.now).transport(), wait=skip_wait),
        # Its own seeded source per root: two test installations get two ids, the same each run.
        installation=InstallationIdFile(
            state.paths.installation_id_file,
            UlidSource(clock, random.Random(str(root)).getrandbits).new_id,  # noqa: S311 -- reproducible test ids
        ),
        local_zone=local_zone,
    )


def skip_wait(_seconds: float, _stopped: Stopped) -> None:
    """Return at once instead of waiting between attempts."""
