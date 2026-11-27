"""Shared fixtures of the desktop tests."""

import logging
import os
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from support.batches import batch_ports, build_batch_tree
from support.credentials import NEVER_SHOWN, TEST_BUCKET, TEST_ENDPOINT, moto_bucket, store_request
from support.discovery import large_tree_names, media_dir
from support.parity import Leg
from support.runpod import Cloud, ManualClock
from support.runs import RunsWorld, runs_ports
from support.storage import PART_BYTES, store_client
from support.trees import build_tree
from transcribe_ui.i18n import system
from transcribe_ui.i18n.catalog import activate
from transcribe_ui.storage.r2 import R2ObjectStore


def pytest_configure() -> None:
    """Let moto serve the test R2 endpoint; run Qt offscreen unless the developer chose a platform.

    moto reads its endpoint once, when it first loads its S3 routes. `QT_QPA_PLATFORM` set
    beforehand (for example to `xcb`) shows the GUI tests' windows.
    """
    os.environ["MOTO_S3_CUSTOM_ENDPOINTS"] = TEST_ENDPOINT
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def english_texts(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Show the English texts in every test, whatever the developer's OS language; reset them afterwards.

    A frontend started with the `system` setting reads no OS language here (English); a test of
    another language activates it itself.
    """
    monkeypatch.setattr(system, "os_language", lambda: None)
    activate("en")
    yield
    activate("en")


@pytest.fixture
def no_credential_logs(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """Capture every log record (DEBUG included) and assert that no credential appears in one."""
    caplog.set_level(logging.DEBUG)
    yield
    for secret in NEVER_SHOWN:
        assert secret not in caplog.text


@pytest.fixture
def r2_store() -> Iterator[R2ObjectStore]:
    """Return the object store on an empty moto bucket, with 5 MiB parts."""
    with moto_bucket():
        yield R2ObjectStore(store_client(), TEST_BUCKET, part_bytes=PART_BYTES)


@pytest.fixture(scope="session")
def large_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return the scan root of a 20 000-file tree, built once per test session (read-only)."""
    root = tmp_path_factory.mktemp("large")
    build_tree(root, large_tree_names())
    return media_dir(root)


@pytest.fixture
def batch_leg(tmp_path: Path) -> Iterator[Leg]:
    """Return a backend with the batch tree, the fake probes and the local zone (closed afterwards)."""
    created = Leg.create(tmp_path / "leg", None, build_batch_tree, batch_ports)
    with closing(created):
        yield created


@pytest.fixture
def batch_store(batch_leg: Leg) -> Iterator[R2ObjectStore]:
    """Serve the test bucket from moto, store its credentials in `batch_leg`; return a store on it."""
    with moto_bucket():
        stored = batch_leg.backend.execute_json(store_request())
        assert stored.status == "ok"
        yield R2ObjectStore(store_client(), TEST_BUCKET)


@pytest.fixture
def runs_world(tmp_path: Path) -> Iterator[RunsWorld]:
    """Return a batch backend with every credential stored, over moto and a fake Runpod API (closed afterwards)."""
    # Real time: the heartbeat rules compare the clock with the `LastModified` moto stamps.
    cloud = Cloud(clock=ManualClock(datetime.now(UTC).replace(microsecond=0)))
    created = Leg.create(tmp_path / "leg", None, build_batch_tree, runs_ports(cloud))
    with closing(created), moto_bucket():
        stored = created.backend.execute_json(store_request())
        assert stored.status == "ok"
        yield RunsWorld(created, cloud, R2ObjectStore(store_client(), TEST_BUCKET))
