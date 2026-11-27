"""Shared fixtures of the desktop tests."""

from pathlib import Path

import pytest

from support.discovery import large_tree_names, media_dir
from support.trees import build_tree


@pytest.fixture(scope="session")
def large_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return the scan root of a 20 000-file tree, built once per test session (read-only)."""
    root = tmp_path_factory.mktemp("large")
    build_tree(root, large_tree_names())
    return media_dir(root)
