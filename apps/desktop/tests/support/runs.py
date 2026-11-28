"""A batch backend over moto and the fake Runpod API, for the manifest, activity and scope tests (M5.9)."""

import json
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path

import httpx

from support.batches import (
    FIRST_BATCH_ID,
    SEEDED_OBJECT_BYTES,
    batch_ports,
    build_batch_tree,
    seed_uploading_batch,
    seeded_keys,
)
from support.credentials import TEST_BUCKET, MemoryKeyring, store_request
from support.parity import Leg, PortsFactory
from support.runpod import Cloud
from support.runpod_api import Failure
from support.storage import store_client
from transcribe_contracts.classes import RunState, RunStatus
from transcribe_contracts.layout import manifest_key, run_status_key
from transcribe_contracts.pods import pod_name
from transcribe_ui.api.ports import CancelFlag, DiscardProgress, Ports, RequestContext
from transcribe_ui.credentials.constants import KEYRING_SERVICE, RUNPOD_API_KEY
from transcribe_ui.runs.activity import check_activity
from transcribe_ui.runs.classes import LaunchScope, RunActivity
from transcribe_ui.runs.cloud import CloudAccess, open_cloud_client
from transcribe_ui.runs.scope import read_launch_scope
from transcribe_ui.state.models import BatchRow, ItemRow, JobRow, RunRow
from transcribe_ui.storage.r2 import R2ObjectStore
from transcribe_ui.upload.manifest import build_manifest, write_manifest
from transcribe_ui.upload.publish import manifest_step

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

# A batch's phase, and whether its manifest is in R2.
type BatchState = tuple[str, bool]

# Upload complete, no manifest yet.
UPLOADING: BatchState = ("uploading", False)
# Manifest published.
READY: BatchState = ("ready", True)


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
        """Open R2 and the Runpod pods API with the stored credentials."""
        with open_cloud_client(self.context()) as (cloud, _client):
            yield cloud

    def activity(self) -> RunActivity:
        """Run the activity check under the runs lock."""
        with self.ports.state.locks.runs(), self.access() as cloud:
            return check_activity(self.ports.state, cloud, self.now())

    def step(self, batch_id: str = FIRST_BATCH_ID) -> None:
        """Run the manifest step under the batch lock (as the upload does)."""
        with self.ports.state.locks.batch(batch_id):
            manifest_step(self.context(), self.store, batch_id)

    def scope(self) -> LaunchScope:
        """Read the launch scope."""
        with self.access() as cloud:
            return read_launch_scope(self.ports.state, cloud.store)


@contextmanager
def installation(cloud: Cloud, root: Path, *, runpod_key: bool) -> Iterator[RunsWorld]:
    """Yield another installation over the same bucket and fake Runpod account (inside `moto_bucket`), closed afterwards.

    Without `runpod_key` only the four R2 credentials are stored (an R2-only installation).
    """
    leg = Leg.create(root, None, build_batch_tree, runs_ports(cloud))
    with closing(leg):
        document = json.loads(store_request())
        if not runpod_key:
            del document[RUNPOD_API_KEY]  # noqa: WPS420 -- the R2-only installation stores no key
        stored = leg.backend.execute_json(json.dumps(document))
        assert stored.status == "ok", stored
        yield RunsWorld(leg, cloud, R2ObjectStore(store_client(), TEST_BUCKET))


def forget_runpod_key(world: RunsWorld) -> None:
    """Remove the stored Runpod key (the installation becomes R2-only, as if the user cleared it)."""
    keyring = world.ports.credentials._make_keyring()  # noqa: SLF001 -- the test keyring behind the store
    assert isinstance(keyring, MemoryKeyring)
    del keyring.entries[KEYRING_SERVICE, RUNPOD_API_KEY]  # noqa: WPS420 -- removing the entry is the point


def runs_ports(cloud: Cloud) -> PortsFactory:
    """Return a ports factory: the batch test ports on the cloud's clock and fake Runpod API."""
    return partial(_ports_on, cloud)


def _ports_on(cloud: Cloud, root: Path) -> Ports:
    return replace(batch_ports(root), clock=cloud.clock, runpod=cloud.access())


def seed_uploaded(world: RunsWorld) -> dict[str, str]:
    """Seed the `uploading` batch with its audio uploaded to the bucket; return file name -> item id."""
    ids = seed_uploading_batch(world.ports.state, world.leg.root)
    for key in seeded_keys(world.ports.state):
        world.store.write(key, b"a" * SEEDED_OBJECT_BYTES, "audio/x-matroska")
    return ids


def seed_ready(world: RunsWorld) -> dict[str, str]:
    """Seed the batch uploaded and published (`ready`, manifest in R2); return file name -> item id."""
    ids = seed_uploaded(world)
    world.step()
    assert batch_state(world) == READY
    return ids


def make_careful(world: RunsWorld, item_id: str) -> None:
    """Switch the published batch's job of the item to `careful` and write its manifest again (before a launch)."""
    with world.ports.state.session() as session:
        row = session.query(JobRow).filter(JobRow.item_id == item_id).one()
        row.payload = {**row.payload, "mode": "careful"}
        manifest = build_manifest(session, row.batch_id)
    write_manifest(world.store, manifest)


def read_back(world: RunsWorld) -> bytes:
    """Return the published manifest's bytes."""
    body = world.store.read(MANIFEST)
    assert body is not None
    return body


def batch_state(world: RunsWorld, batch_id: str = FIRST_BATCH_ID) -> BatchState:
    """Return the batch's phase, and whether its manifest is in R2."""
    with world.ports.state.session() as session:
        row = session.get(BatchRow, batch_id)
        assert row is not None
        phase = row.phase
    return phase, world.store.head(manifest_key(batch_id)) is not None


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


def seed_heartbeat(
    world: RunsWorld,
    state: RunState,
    updated_at: datetime,
    run_id: str = RUN_ID,
    deadline_at: datetime | None = None,
) -> str:
    """Write a worker `status.json` for the run (deadline an hour after its start unless given); return its key."""
    started = min(updated_at, deadline_at or updated_at) - timedelta(minutes=1)
    status = RunStatus(
        run_id=run_id,
        pod_id=HEARTBEAT_POD_ID,
        state=state,
        started_at=started,
        updated_at=updated_at,
        deadline_at=deadline_at or started + timedelta(hours=1),
        done_jobs=0,
        total_jobs=1,
        error="worker failed" if state == "failed" else None,
    )
    key = run_status_key(run_id)
    world.store.write(key, status.model_dump_json().encode(), JSON_TYPE)
    return key
