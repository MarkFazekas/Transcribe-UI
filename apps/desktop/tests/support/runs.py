"""A batch backend over moto and the fake Runpod API, for the manifest, activity and scope tests (M5.9)."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path

import httpx

from support.batches import FIRST_BATCH_ID, SEEDED_OBJECT_BYTES, batch_ports, seed_uploading_batch, seeded_keys
from support.parity import Leg, PortsFactory
from support.runpod import Cloud
from support.runpod_api import Failure
from transcribe_contracts.classes import RunState, RunStatus
from transcribe_contracts.layout import manifest_key, run_status_key
from transcribe_contracts.pods import pod_name
from transcribe_ui.api.ports import CancelFlag, DiscardProgress, Ports, RequestContext
from transcribe_ui.runs.activity import check_activity
from transcribe_ui.runs.classes import LaunchScope, RunActivity
from transcribe_ui.runs.cloud import CloudAccess, open_cloud
from transcribe_ui.runs.scope import read_launch_scope
from transcribe_ui.state.models import BatchRow, ItemRow, RunRow
from transcribe_ui.storage.r2 import R2ObjectStore
from transcribe_ui.upload.classes import HeldPublication, ManifestStep
from transcribe_ui.upload.publish import manifest_step, publish_held_manifests

# Run id of most tests, a canonical ULID.
RUN_ID = "01JDZZZZZZZZZZZZZZZZZZZR01"
# A second run id.
OTHER_RUN_ID = "01JDZZZZZZZZZZZZZZZZZZZR02"
# Pod id the seeded heartbeats name.
HEARTBEAT_POD_ID = "pod-heartbeat"
# How long after "now" the pod names of the tests expire.
NAME_DEADLINE = timedelta(hours=3)
# Key of the seeded batch's manifest.
MANIFEST = manifest_key(FIRST_BATCH_ID)
# Content type of the JSON objects the tests write.
JSON_TYPE = "application/json"

# A server error for every attempt of one call (the client tries 5 times).
OUTAGE = (Failure(status=httpx.codes.SERVICE_UNAVAILABLE),) * 5

# A batch's phase, `manifest_held`, and whether its manifest is in R2.
type BatchState = tuple[str, bool, bool]


def app_pod_name(run_id: str, now: datetime) -> str:
    """Return the app's pod name of a run, with its deadline `NAME_DEADLINE` from now."""
    return pod_name(run_id, now + NAME_DEADLINE)


@dataclass
class RunsWorld:
    """One backend (batch tree, fake media) over the moto bucket and a fake Runpod API with a manual clock."""

    leg: Leg
    cloud: Cloud
    store: R2ObjectStore

    @property
    def ports(self) -> Ports:
        """Return the backend's ports."""
        return self.leg.ports

    def now(self) -> datetime:
        """Return the current test time."""
        return self.cloud.clock.now()

    def context(self, cancel: CancelFlag | None = None) -> RequestContext:
        """Return a request context on the backend's ports."""
        return RequestContext(self.ports, DiscardProgress(), cancel or CancelFlag())

    @contextmanager
    def access(self) -> Iterator[CloudAccess]:
        """Open the cloud with the stored credentials."""
        with open_cloud(self.context()) as cloud:
            yield cloud

    def activity(self) -> RunActivity:
        """Run the activity check under the runs lock."""
        with self.ports.state.locks.runs(), self.access() as cloud:
            return check_activity(self.ports.state, cloud, self.now())

    def step(self, batch_id: str = FIRST_BATCH_ID) -> ManifestStep:
        """Run the manifest step under the batch lock (as the upload does)."""
        with self.ports.state.locks.batch(batch_id), self.access() as cloud:
            return manifest_step(self.context(), cloud, batch_id)

    def publish_held(self) -> HeldPublication:
        """Run `publish_held_manifests` under the runs lock (as launch and refresh do)."""
        with self.ports.state.locks.runs(), self.access() as cloud:
            return publish_held_manifests(self.context(), cloud)

    def scope(self) -> LaunchScope:
        """Read the launch scope."""
        with self.access() as cloud:
            return read_launch_scope(self.ports.state, cloud.store)


def runs_ports(cloud: Cloud) -> PortsFactory:
    """Return a ports factory: the batch test ports on the cloud's clock and fake Runpod API."""
    return partial(_ports_on, cloud)


def _ports_on(cloud: Cloud, root: Path) -> Ports:
    return replace(batch_ports(root), clock=cloud.clock, runpod=cloud.access())


def seed_uploaded(world: RunsWorld, *, held: bool = False) -> dict[str, str]:
    """Seed the `uploading` batch with its audio uploaded to the bucket; return file name -> item id."""
    ids = seed_uploading_batch(world.ports.state, world.leg.root, manifest_held=held)
    for key in seeded_keys(world.ports.state):
        world.store.write(key, b"a" * SEEDED_OBJECT_BYTES, "audio/x-matroska")
    return ids


def batch_state(world: RunsWorld, batch_id: str = FIRST_BATCH_ID) -> BatchState:
    """Return the batch's phase and `manifest_held`, and whether its manifest is in R2."""
    with world.ports.state.session() as session:
        row = session.get(BatchRow, batch_id)
        assert row is not None
        phase, held = row.phase, row.manifest_held
    return phase, held, world.store.head(manifest_key(batch_id)) is not None


def set_item_phase(world: RunsWorld, item_id: str, phase: str) -> None:
    """Change an item's phase directly."""
    with world.ports.state.session() as session:
        row = session.get(ItemRow, item_id)
        assert row is not None
        row.phase = phase


def seed_run(world: RunsWorld, status: str, run_id: str = RUN_ID) -> None:
    """Store a local run row with this status."""
    now = world.now()
    row = RunRow(
        run_id=run_id,
        status=status,
        pod_name=app_pod_name(run_id, now),
        created_at=now,
        updated_at=now,
        payload={},
    )
    with world.ports.state.session() as session:
        session.add(row)


def set_run_status(world: RunsWorld, status: str, run_id: str = RUN_ID) -> None:
    """Change a local run's status."""
    with world.ports.state.session() as session:
        row = session.get(RunRow, run_id)
        assert row is not None
        row.status = status


def seed_heartbeat(world: RunsWorld, state: RunState, updated_at: datetime, run_id: str = RUN_ID) -> str:
    """Write a worker `status.json` for the run; return its key."""
    started = updated_at - timedelta(minutes=1)
    status = RunStatus(
        run_id=run_id,
        pod_id=HEARTBEAT_POD_ID,
        state=state,
        started_at=started,
        updated_at=updated_at,
        deadline_at=started + timedelta(hours=1),
        done_jobs=0,
        total_jobs=1,
        error="worker failed" if state == "failed" else None,
    )
    key = run_status_key(run_id)
    world.store.write(key, status.model_dump_json().encode(), JSON_TYPE)
    return key
