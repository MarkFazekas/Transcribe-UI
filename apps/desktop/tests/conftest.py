"""Shared fixtures of the desktop tests."""

import logging
import os
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from support.batches import batch_ports, build_batch_tree
from support.credentials import NEVER_SHOWN, TEST_BUCKET, TEST_ENDPOINT, moto_bucket, store_request
from support.discovery import large_tree_names, media_dir
from support.parity import Leg
from support.storage import PART_BYTES, store_client
from support.trees import build_tree
from transcribe_ui.storage.r2 import R2ObjectStore


def pytest_configure() -> None:
    """Let moto serve the test R2 endpoint (read once, when moto first loads its S3 routes)."""
    os.environ["MOTO_S3_CUSTOM_ENDPOINTS"] = TEST_ENDPOINT


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
