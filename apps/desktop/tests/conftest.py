"""Shared fixtures of the desktop tests."""

import os
from pathlib import Path

import pytest

from support.credentials import TEST_ENDPOINT
from support.discovery import large_tree_names, media_dir
from support.trees import build_tree


def pytest_configure() -> None:
    """Let moto serve the test R2 endpoint (read once, when moto first loads its S3 routes)."""
    os.environ["MOTO_S3_CUSTOM_ENDPOINTS"] = TEST_ENDPOINT


@pytest.fixture(scope="session")
def large_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return the scan root of a 20 000-file tree, built once per test session (read-only)."""
    root = tmp_path_factory.mktemp("large")
    build_tree(root, large_tree_names())
    return media_dir(root)
