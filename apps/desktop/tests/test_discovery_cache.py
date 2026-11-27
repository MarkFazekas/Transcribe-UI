"""`preview_selection` reusing a kept scan by `scan_id` (architecture §3.2)."""

import json
from pathlib import Path

from support.discovery import (
    FILM,
    MEDIA_ROOT,
    build_media_tree,
    file_names,
    include,
    media_dir,
    response_of,
    scan_request,
)
from support.fakes import seeded_ports
from support.parity import run_parity
from transcribe_ui.api.backend import Backend
from transcribe_ui.discovery.classes import (
    PreviewSelectionRequest,
    PreviewSelectionResponse,
    ScanTreeRequest,
    ScanTreeResponse,
)

# A well-formed ULID no scan has.
UNKNOWN_SCAN_ID = "01KB00000000000000000000ZZ"


def _preview_document(root: str, scan_id: str | None, **scan: object) -> dict[str, object]:
    return {
        "action": "preview_selection",
        "scan": {"root": root, **scan},
        "scan_id": scan_id,
        "operations": [include(root)],
    }


def _first_scan_id(root: Path) -> str:
    # Only the id source is used; nothing is written under `root`.
    return seeded_ports(root).ids.new_id()


def test_preview_reuses_the_scan_on_both_legs(tmp_path: Path) -> None:
    """A preview naming the scan just made answers from it, through the backend and the CLI."""
    preview = json.dumps(_preview_document(MEDIA_ROOT, _first_scan_id(tmp_path)))
    scanned, previewed = run_parity(tmp_path, [scan_request(), preview], prepare=build_media_tree)
    scan_id = response_of(scanned)["scan_id"]
    assert scan_id == _first_scan_id(tmp_path)
    assert response_of(previewed)["scan_id"] == scan_id
    assert len(file_names(previewed)) == len(file_names(scanned))


def _preview(backend: Backend, root: Path, scan_id: str | None, **scan: object) -> PreviewSelectionResponse:
    request = PreviewSelectionRequest.model_validate(_preview_document(str(root), scan_id, **scan))
    response = backend.execute(request)
    assert isinstance(response, PreviewSelectionResponse), response
    return response


def test_kept_scan_is_used_and_unknown_ids_scan_anew(tmp_path: Path) -> None:
    """The kept scan is used as it was; an unknown id or other options scan anew."""
    build_media_tree(tmp_path)
    root = media_dir(tmp_path)
    backend = Backend(seeded_ports(tmp_path))
    scanned = backend.execute(ScanTreeRequest(action="scan_tree", root=str(root)))
    assert isinstance(scanned, ScanTreeResponse)
    (root / "Movies" / FILM).unlink()

    cached = _preview(backend, root, scanned.scan_id)
    assert cached.scan_id == scanned.scan_id
    assert FILM in [found.name for found in cached.files]

    for fresh in (
        _preview(backend, root, None),
        _preview(backend, root, UNKNOWN_SCAN_ID),
        _preview(backend, root, scanned.scan_id, include_hidden=True),
    ):
        assert fresh.scan_id != scanned.scan_id
        assert FILM not in [found.name for found in fresh.files]
