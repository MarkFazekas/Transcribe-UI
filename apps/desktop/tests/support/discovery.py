"""Shared media tree and request builders of the discovery tests (plan m5 §3 M5.3)."""

import dataclasses
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from support.fakes import seeded_ports
from support.parity import Exchange
from support.trees import build_tree
from transcribe_ui.api.backend import Backend
from transcribe_ui.discovery.classes import ScanTreeRequest, ScanTreeResponse
from transcribe_ui.discovery.rules import PathRules

# Name of the scanned folder inside each leg's root: fixed, so the root folder's name matches on both legs.
MEDIA = "media"
# The scan root of the request documents.
MEDIA_ROOT = f"<root>/{MEDIA}"

# Large-tree regression guard: folders x media files per folder.
LARGE_TREE_SHAPE = (200, 100)
# Time budget of one request over the large tree (generous: a regression guard, not a benchmark).
LARGE_TREE_BUDGET_S = 30.0

# A JSON object of a request document.
type JsonObject = dict[str, object]

# Every case of the scan: nested folders, duplicate stems with several sidecars, language forms,
# forced and unknown-language sidecars, a non-ASCII name, an orphan subtitle, a folder without
# media, hidden entries and an upper-case extension.
MEDIA_TREE = (
    "media/Movies/Film.mkv",
    "media/Movies/Film.mp4",
    "media/Movies/Film.en.srt",
    "media/Movies/Film.hun.forced.srt",
    "media/Movies/Film.Hungarian.srt",
    "media/Movies/.Film.de.srt",
    "media/Movies/Film.part2.mkv",
    "media/Movies/Film.part2.pt-BR.vtt",
    "media/Movies/Film.part2.srt",
    "media/Movies/Ünnepi műsor.mkv",
    "media/Movies/Ünnepi műsor.de.ass",
    "media/Movies/orphan.en.srt",
    "media/Movies/notes.txt",
    "media/Series/S01/Show S01E01.mkv",
    "media/Series/S01/Show S01E01.en.srt",
    "media/Series/S01/Show S01E02.mkv",
    "media/Series/S01/Show S01E02.hu.srt",
    "media/Series/S01/Show S01E02.en.srt",
    "media/Series/S01/Extras/Show behind the scenes.webm",
    "media/Audio/podcast.MP3",
    "media/Audio/interview.flac",
    "media/Audio/interview.en.srt",
    "media/Empty/readme.txt",
    "media/.hidden/secret.mkv",
    "media/.cover.mp4",
)

# Media files of `MEDIA_TREE` a default scan finds, in scan order.
INTERVIEW = "interview.flac"
PODCAST = "podcast.MP3"
FILM = "Film.mkv"
FILM_TWIN = "Film.mp4"
FILM_SEQUEL = "Film.part2.mkv"
FESTIVE = "Ünnepi műsor.mkv"
EPISODE_ONE = "Show S01E01.mkv"
EPISODE_TWO = "Show S01E02.mkv"
EXTRAS = "Show behind the scenes.webm"
EVERY_FILE = (INTERVIEW, PODCAST, FILM, FILM_TWIN, FILM_SEQUEL, FESTIVE, EPISODE_ONE, EPISODE_TWO, EXTRAS)


def build_media_tree(root: Path) -> None:
    """Create `MEDIA_TREE` under the root."""
    build_tree(root, MEDIA_TREE)


def media_dir(root: Path) -> Path:
    """Return the scanned folder inside a root."""
    return root / MEDIA


def scan_request(root: str = MEDIA_ROOT, **fields: object) -> str:
    """Return a `scan_tree` request document."""
    return json.dumps({"action": "scan_tree", "root": root, **fields})


def include(path: str, **filters: object) -> JsonObject:
    """Return an include operation with the given filters."""
    return {"operation": "include", "path": path, "filters": filters}


def exclude(path: str, **filters: object) -> JsonObject:
    """Return an exclude operation with the given filters."""
    return {"operation": "exclude", "path": path, "filters": filters}


def subtitles(rule: str, *languages: str) -> JsonObject:
    """Return a subtitle filter."""
    return {"languages": list(languages), "rule": rule}


def name_filter(match: str, text: str) -> JsonObject:
    """Return a name filter."""
    return {"match": match, "text": text}


def preview_request(operations: Sequence[JsonObject]) -> str:
    """Return a `preview_selection` request document scanning `MEDIA_ROOT`."""
    spec = {"root": MEDIA_ROOT}
    return json.dumps({"action": "preview_selection", "scan": spec, "operations": list(operations)})


def response_of(exchange: Exchange) -> dict[str, Any]:
    """Return the exchange's response as JSON data."""
    document: dict[str, Any] = json.loads(exchange.response)
    return document


def file_names(exchange: Exchange) -> list[str]:
    """Return the names of the files in the exchange's response, in order."""
    return [found["name"] for found in response_of(exchange)["files"]]


def warning_codes(exchange: Exchange) -> list[str]:
    """Return the codes of the warnings in the exchange's response, in order."""
    return [warning["code"] for warning in response_of(exchange)["warnings"]]


def error_code(exchange: Exchange) -> str:
    """Return the error code of the exchange's response."""
    code: str = response_of(exchange)["error"]["code"]
    return code


def large_tree_names() -> list[str]:
    """Return the relative paths of the large tree's media files."""
    folders, per_folder = LARGE_TREE_SHAPE
    return [f"media/folder{folder:03}/clip{number:03}.mkv" for folder in range(folders) for number in range(per_folder)]


def scan_direct(root: Path, rules: PathRules | None = None, **fields: object) -> ScanTreeResponse:
    """Scan through a fresh seeded backend (no CLI leg), with another path rule if given."""
    # Scans need no database, so nothing is written under the root's state folder.
    ports = seeded_ports(root)
    if rules is not None:
        ports = dataclasses.replace(ports, path_rules=rules)
    request = ScanTreeRequest.model_validate({"action": "scan_tree", "root": str(root), **fields})
    response = Backend(ports).execute(request)
    assert isinstance(response, ScanTreeResponse), response
    return response
