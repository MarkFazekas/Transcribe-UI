"""Parity legs of the launch tests: per leg its own moto bucket and fake Runpod API, set up alike (plan m5 M5.11)."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import boto3

from support.batches import SEEDED_OBJECT_BYTES, batch_ports, seed_uploading_batch, seeded_keys
from support.credentials import TEST_ACCESS_KEY_ID, TEST_ENDPOINT, TEST_SECRET
from support.launch import write_foreign
from support.runpod import Cloud, credential_store
from support.runpod_api import FAKE_API_KEY
from support.storage import r2_credentials
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.ports import Ports
from transcribe_ui.runpod.classes import SetupCloudRequest
from transcribe_ui.state.store import LocalState
from transcribe_ui.storage.client import r2_client
from transcribe_ui.storage.r2 import R2ObjectStore

# Seeds one parity leg: its ports, its bucket and its fake Runpod API.
type LegSeed = Callable[[Ports, Path, R2ObjectStore, Cloud], None]

# Prefix of the parity legs' buckets.
LEG_BUCKET_PREFIX = "transcribe-leg-"


def create_bucket(name: str) -> None:
    """Create a bucket in the active moto mock (moto refuses region `auto`, so the setup client uses another)."""
    setup = boto3.session.Session().client(
        "s3",
        region_name="us-east-1",
        endpoint_url=TEST_ENDPOINT,
        aws_access_key_id="setup",
        aws_secret_access_key="setup",  # noqa: S106 -- moto ignores credentials.
    )
    setup.create_bucket(Bucket=name)


def seed_held(state: LocalState, root: Path, store: R2ObjectStore) -> None:
    """Seed the held batch (upload complete, manifest held) with its audio in the bucket."""
    seed_uploading_batch(state, root, manifest_held=True)
    for key in seeded_keys(state):
        store.write(key, b"a" * SEEDED_OBJECT_BYTES, "audio/x-matroska")


@dataclass
class LaunchLegs:
    """Ports factory of the parity runner: per leg its own bucket and fake Runpod API, set up alike.

    Each leg's cloud setup is created through its own backend, then `seed` fills the leg.
    """

    seed: LegSeed | None = None
    # The clouds of the legs, in creation order (backend leg first).
    clouds: list[Cloud] = field(default_factory=list)
    # The legs' object stores, in the same order.
    stores: list[R2ObjectStore] = field(default_factory=list)

    def __call__(self, root: Path) -> Ports:
        """Return the ports of one leg (the moto mock must be active)."""
        bucket = f"{LEG_BUCKET_PREFIX}{len(self.clouds)}"
        create_bucket(bucket)
        cloud = Cloud()
        stored = {
            "r2_endpoint_url": TEST_ENDPOINT,
            "r2_bucket": bucket,
            "r2_access_key_id": TEST_ACCESS_KEY_ID,
            "r2_secret_access_key": TEST_SECRET,
            "runpod_api_key": FAKE_API_KEY,
        }
        ports = replace(
            batch_ports(root), clock=cloud.clock, runpod=cloud.access(), credentials=credential_store(stored)
        )
        set_up = Backend(ports).execute(SetupCloudRequest(action="setup_cloud"))
        assert set_up.status == "ok", set_up
        store = R2ObjectStore(r2_client(r2_credentials(bucket)), bucket)
        if self.seed is not None:
            self.seed(ports, root, store, cloud)
        self.clouds.append(cloud)
        self.stores.append(store)
        return ports


def seed_held_leg(ports: Ports, root: Path, store: R2ObjectStore, _cloud: Cloud) -> None:
    """Seed a parity leg with the held batch and another computer's manifest."""
    seed_held(ports.state, root, store)
    write_foreign(store)
