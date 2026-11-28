"""Regression tests of the R2 data contract.

Schema snapshots make every contract change a visible diff; regenerate them with
`UPDATE_SNAPSHOTS=1 uv run pytest packages/contracts/tests`. The snapshots do not capture
validators, so each validator also has a behaviour test below.
"""

import json
import os
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from transcribe_contracts import layout
from transcribe_contracts.classes import BatchSummary, JobResult, Manifest, RunLease, RunStatus
from transcribe_contracts.constants import ULID_PATTERN, ULID_RANDOM_BITS
from transcribe_contracts.errors import ContractError, UnsupportedSchemaError
from transcribe_contracts.ids import new_ulid
from transcribe_contracts.reading import (
    read_batch_summary,
    read_job_result,
    read_manifest,
    read_run_lease,
    read_run_status,
)

TESTS_DIR = Path(__file__).parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
SNAPSHOTS_DIR = TESTS_DIR / "snapshots"
BATCH_ID = "batch_20261126_143005"
ITEM_ID = "01JDA3Q5Z8V9K2M4N6P8R0T2W4"
AUDIO_ITEM_ID = "01JDA3Q5Z8V9K2M4N6P8R0T2W5"
RUN_ID = "01JDA4B7C8D9E0F1G2H3J4K5M6"
HUNGARIAN = "hu"
MANIFEST_KEY = layout.manifest_key(BATCH_ID)
RESULT_KEY = layout.result_json_key(BATCH_ID, ITEM_ID, HUNGARIAN)
SUMMARY_KEY = layout.done_key(BATCH_ID)
STATUS_KEY = layout.run_status_key(RUN_ID)
LEASE_KEY = layout.run_lease_key(RUN_ID)
MANIFEST_FIXTURE = "manifest.json"
DOT_PART_MESSAGE = r"has a '\.' or '\.\.' part"
FOREIGN_KEY = f"other_batch/audio/{ITEM_ID}.a0.mka"
# The ULID spec's example time (1469918176385 ms) and its ULID with zero randomness.
ULID_SPEC_MOMENT = datetime.fromisoformat("2016-07-30T22:36:16.385+00:00")
ULID_SPEC_ZERO_RANDOM = "01ARYZ6S410000000000000000"

type Reader = Callable[[str, bytes | str], BaseModel]

READERS: tuple[tuple[Reader, str, str], ...] = (
    (read_manifest, MANIFEST_FIXTURE, MANIFEST_KEY),
    (read_job_result, "job_result.json", RESULT_KEY),
    (read_batch_summary, "batch_summary.json", SUMMARY_KEY),
    (read_run_status, "run_status.json", STATUS_KEY),
    (read_run_lease, "run_lease.json", LEASE_KEY),
)
READER_PARAMETERS = ("reader", "fixture", "key")


def _load(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    return loaded


def _path_part(part: str) -> int | str:
    return int(part) if part.isdigit() else part


def _changed(name: str, changes: dict[str, object]) -> str:
    """Return a fixture as JSON text with values replaced at dotted paths (`items.0.item_id`)."""
    document = _load(name)
    for path, new in changes.items():
        parts = [_path_part(part) for part in path.split(".")]
        *parents, leaf = parts
        node: Any = document
        for part in parents:
            node = node[part]
        node[leaf] = new
    return json.dumps(document)


@pytest.mark.parametrize("model", [Manifest, JobResult, BatchSummary, RunStatus, RunLease])
def test_json_schema_matches_snapshot(model: type[BaseModel]) -> None:
    """The JSON Schema of each stored object equals the committed snapshot."""
    snapshot = SNAPSHOTS_DIR / f"{model.__name__}.schema.json"
    schema = json.dumps(model.model_json_schema(), indent=2, sort_keys=True)
    rendered = f"{schema}\n"
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(rendered, encoding="utf-8")
    assert rendered == snapshot.read_text(encoding="utf-8")


@pytest.mark.parametrize(READER_PARAMETERS, READERS)
def test_fixture_round_trips_unchanged(reader: Reader, fixture: str, key: str) -> None:
    """A stored object reads and serialises back to the same JSON value."""
    raw = _load(fixture)
    read_back = reader(key, json.dumps(raw))
    assert json.loads(read_back.model_dump_json()) == raw


@pytest.mark.parametrize(READER_PARAMETERS, READERS)
def test_newer_minor_with_new_fields_is_accepted(reader: Reader, fixture: str, key: str) -> None:
    """A newer minor version may add fields, also nested ones; the current reader accepts them."""
    raw = _load(fixture) | {"schema_version": "1.7", "added_later": {"nested": True}}
    read_back = reader(key, json.dumps(raw))
    assert read_back.model_dump()["schema_version"] == "1.7"


@pytest.mark.parametrize(READER_PARAMETERS, READERS)
def test_other_major_is_refused_before_validation(reader: Reader, fixture: str, key: str) -> None:
    """Another major version is reported as such, even when the structure changed completely."""
    body = json.dumps({"schema_version": "2.0", "renamed": _load(fixture)})
    with pytest.raises(UnsupportedSchemaError, match="not supported"):
        reader(key, body)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("not json", "not valid JSON"),
        ("[]", "not a JSON object"),
        ("{}", "schema_version is missing"),
        ('{"schema_version": "01.0"}', "is not '<major>.<minor>'"),
    ],
)
def test_malformed_object_is_refused(body: str, message: str) -> None:
    """Broken objects raise ContractError, never a raw parsing exception."""
    with pytest.raises(ContractError, match=message):
        read_manifest(MANIFEST_KEY, body)


@pytest.mark.parametrize(
    READER_PARAMETERS,
    [
        (read_manifest, MANIFEST_FIXTURE, layout.manifest_key("batch_20261126_143006")),
        (read_job_result, "job_result.json", layout.result_json_key(BATCH_ID, ITEM_ID, "en")),
        (read_job_result, "job_result.json", layout.result_srt_key(BATCH_ID, ITEM_ID, HUNGARIAN)),
        (read_batch_summary, "batch_summary.json", layout.done_key("batch_20261126_143006")),
        (read_run_status, "run_status.json", layout.run_status_key(ITEM_ID)),
        (read_run_lease, "run_lease.json", layout.run_lease_key(ITEM_ID)),
    ],
)
def test_object_under_a_foreign_key_is_refused(reader: Reader, fixture: str, key: str) -> None:
    """An object is only valid at the key its content names."""
    with pytest.raises(ContractError, match="does not belong there"):
        reader(key, json.dumps(_load(fixture)))


DUPLICATE_AUDIO = _load(MANIFEST_FIXTURE)["items"][0]["audio"][0]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"items.0.audio.0.object_key": FOREIGN_KEY, "items.0.jobs.0.audio_object_key": FOREIGN_KEY}, "must be"),
        ({"items.0.audio.1": DUPLICATE_AUDIO}, "same audio object twice"),
        ({"items.0.audio.1.track_index": None}, "one audio object per distinct track"),
        ({"items.1.audio.0.track_index": 0}, "exactly one audio object without a track"),
        ({"items.0.jobs.1.language": "hun1"}, "should match pattern"),
        ({"items.0.jobs.1.language": HUNGARIAN}, "more than one job for the same language"),
        ({"items.0.jobs.1.audio_object_key": f"{BATCH_ID}/audio/{ITEM_ID}.a9.mka"}, "does not list"),
        ({"items.1.item_id": ITEM_ID}, "item ids are not unique"),
        ({"items.1.item_id": AUDIO_ITEM_ID.lower()}, "should match pattern"),
        ({"items.0.audio.0.extension": "MKA"}, "should match pattern"),
        ({"batch_id": "batch_2026112\N{ARABIC-INDIC DIGIT SIX}_143005"}, "should match pattern"),
        ({"created_at": "2026-11-26T14:30:05"}, "timezone"),
        ({"scan_root": "Media"}, "not an absolute windows path"),
        ({"items.1.original_path": r"E:\Other\Ep 12.mp3"}, "not under scan_root"),
        ({"items.1.original_path": r"D:\Media\..\Other\Ep 12.mp3"}, DOT_PART_MESSAGE),
        ({"items.1.original_path": r"D:\Media\Podcasts/../../Ep 12.mp3"}, DOT_PART_MESSAGE),
        ({"items.1.original_path": r"D:\Media\.\Ep 12.mp3"}, DOT_PART_MESSAGE),
        ({"scan_root": r"D:\Media\.."}, DOT_PART_MESSAGE),
        ({"uploaded_by": ""}, "at least 1 character"),
    ],
)
def test_invalid_manifest_is_refused(changes: dict[str, object], message: str) -> None:
    """The worker never processes a manifest that breaks the contract."""
    with pytest.raises(ContractError, match=message):
        read_manifest(MANIFEST_KEY, _changed(MANIFEST_FIXTURE, changes))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"status": "failed"}, "error must be set"),
        ({"error": "boom"}, "error must be set"),
        ({"status": "no_speech"}, "cue_count must be positive exactly"),
        ({"cue_count": 0}, "cue_count must be positive exactly"),
        ({"finished_at": "2026-11-26T15:00:00Z"}, "before started_at"),
    ],
)
def test_inconsistent_job_result_is_refused(changes: dict[str, object], message: str) -> None:
    """A job result's status, error and cue count must agree."""
    with pytest.raises(ContractError, match=message):
        read_job_result(RESULT_KEY, _changed("job_result.json", changes))


def test_language_outside_whisper_set_is_accepted() -> None:
    """A future worker may support more languages; old readers keep working."""
    key = layout.result_json_key(BATCH_ID, ITEM_ID, "xyz")
    body = _changed("job_result.json", {"language": "xyz"})
    assert read_job_result(key, body).language == "xyz"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"done_jobs": 4}, "exceeds total_jobs"),
        ({"updated_at": "2026-11-26T14:00:00Z"}, "updated_at is before started_at"),
        ({"deadline_at": "2026-11-26T15:00:00Z"}, "deadline_at must be after started_at"),
        ({"state": "failed"}, "error must be set exactly"),
        ({"error": "boom"}, "error must be set exactly"),
        ({"state": "finished"}, "has no current job"),
        ({"pod_id": ""}, "at least 1 character"),
    ],
)
def test_inconsistent_run_status_is_refused(changes: dict[str, object], message: str) -> None:
    """The desktop app and the reaper rely on a coherent heartbeat."""
    with pytest.raises(ContractError, match=message):
        read_run_status(STATUS_KEY, _changed("run_status.json", changes))


@pytest.mark.parametrize(
    ("original_path", "refused"),
    [
        ("/media/../etc/x.mkv", True),
        ("/media/./x.mkv", True),
        ("/media/..x/x.mkv", False),
        ("/media/x..mkv", False),
        (r"/media/..\x.mkv", False),
    ],
)
def test_posix_manifest_dot_parts(original_path: str, *, refused: bool) -> None:
    r"""A POSIX path splits only at `/`: `..\x` is a name there (the desktop's adoption refuses it)."""
    changes: dict[str, object] = {
        "scan_root": "/media",
        "path_flavor": "posix",
        "items.0.original_path": original_path,
        "items.1.original_path": "/media/ep.mp3",
    }
    body = _changed(MANIFEST_FIXTURE, changes)
    if refused:
        with pytest.raises(ContractError, match=DOT_PART_MESSAGE):
            read_manifest(MANIFEST_KEY, body)
    else:
        assert read_manifest(MANIFEST_KEY, body).items[0].original_path == original_path


def test_manifest_without_installation_id_reads() -> None:
    """A 1.0 manifest has no `uploaded_by`; it reads as None."""
    document = {key: field for key, field in _load(MANIFEST_FIXTURE).items() if key != "uploaded_by"}
    document["schema_version"] = "1.0"
    assert read_manifest(MANIFEST_KEY, json.dumps(document)).uploaded_by is None


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"batch_ids": []}, "at least 1 item"),
        ({"batch_ids.1": BATCH_ID}, "batch ids are not unique"),
        ({"batch_ids.0": "batch_x"}, "should match pattern"),
        ({"deadline_at": "2026-11-27T17:00:00"}, "timezone"),
    ],
)
def test_invalid_run_lease_is_refused(changes: dict[str, object], message: str) -> None:
    """A lease the worker cannot trust is refused (the worker then processes nothing)."""
    with pytest.raises(ContractError, match=message):
        read_run_lease(LEASE_KEY, _changed("run_lease.json", changes))


def test_object_keys_follow_the_layout() -> None:
    """Every key of architecture §4, and the round trips of the parsers."""
    keys = [
        layout.manifest_key(BATCH_ID),
        layout.batch_prefix(BATCH_ID),
        layout.audio_key(BATCH_ID, ITEM_ID, "mka", 2),
        layout.audio_key(BATCH_ID, ITEM_ID, "mp3", None),
        layout.result_srt_key(BATCH_ID, ITEM_ID, HUNGARIAN),
        layout.result_json_key(BATCH_ID, ITEM_ID, "haw"),
        layout.done_key(BATCH_ID),
        layout.run_status_key(RUN_ID),
        layout.run_log_key(RUN_ID),
        layout.run_lease_key(RUN_ID),
    ]
    assert keys == [
        f"batch_metadata/{BATCH_ID}.json",
        f"{BATCH_ID}/",
        f"{BATCH_ID}/audio/{ITEM_ID}.a2.mka",
        f"{BATCH_ID}/audio/{ITEM_ID}.mp3",
        f"{BATCH_ID}/results/{ITEM_ID}.hu.srt",
        f"{BATCH_ID}/results/{ITEM_ID}.haw.json",
        f"{BATCH_ID}/_done.json",
        f"runs/{RUN_ID}/status.json",
        f"runs/{RUN_ID}/worker.log",
        f"runs/{RUN_ID}/launch.json",
    ]
    assert layout.run_id_from_lease_key(keys[-1]) == RUN_ID
    assert layout.batch_id_from_manifest_key(keys[0]) == BATCH_ID
    assert layout.parse_result_key(keys[5]) == (BATCH_ID, ITEM_ID, "haw", ".json")


@pytest.mark.parametrize(
    "key",
    [
        "batch_metadata/",
        "batch_metadata/.json",
        "batch_metadata/x.json",
        "batch_metadata/...json",
        f"batch_metadata/a/{BATCH_ID}.json",
        f"other/{BATCH_ID}.json",
        f"batch_metadata/{BATCH_ID}.txt",
    ],
)
def test_malformed_manifest_keys_are_ignored(key: str) -> None:
    """Listing `batch_metadata/` only yields well-formed batch ids."""
    assert layout.batch_id_from_manifest_key(key) is None


@pytest.mark.parametrize(
    "key",
    [
        "runs/launch.json",
        f"runs/{RUN_ID}/status.json",
        f"runs/{RUN_ID.lower()}/launch.json",
        f"runs/a/{RUN_ID}/launch.json",
        f"other/{RUN_ID}/launch.json",
    ],
)
def test_malformed_lease_keys_are_ignored(key: str) -> None:
    """Listing `runs/` only yields leases of well-formed run ids."""
    assert layout.run_id_from_lease_key(key) is None


@pytest.mark.parametrize(
    "key",
    [
        f"{BATCH_ID}/results/{ITEM_ID}.json",
        f"{BATCH_ID}/results/{ITEM_ID}.hu.forced.srt",
        f"{BATCH_ID}/results/sub/{ITEM_ID}.hu.json",
        f"{BATCH_ID}/audio/{ITEM_ID}.hu.json",
        f"x/results/{ITEM_ID}.hu.json",
    ],
)
def test_malformed_result_keys_are_ignored(key: str) -> None:
    """Only `<batch_id>/results/<item_id>.<language>.(json|srt)` is a result key."""
    assert layout.parse_result_key(key) is None


@pytest.mark.parametrize("segment", ["", "..", ".hidden", "a/b", r"a\b"])
def test_key_builders_refuse_escaping_segments(segment: str) -> None:
    """A key segment can never leave its folder."""
    with pytest.raises(ValueError, match="invalid key segment"):
        layout.result_srt_key(BATCH_ID, segment, HUNGARIAN)


def test_new_ulid_matches_the_spec_example() -> None:
    """Known value: time 1469918176385 ms, randomness 0 (ULID spec), and the maximum."""
    assert new_ulid(ULID_SPEC_MOMENT, randomness=0) == ULID_SPEC_ZERO_RANDOM
    assert re.fullmatch(ULID_PATTERN, new_ulid(ULID_SPEC_MOMENT)) is not None


@pytest.mark.parametrize(
    ("moment", "randomness"),
    [
        (datetime.fromisoformat("2026-01-01T00:00:00"), 0),
        (datetime.fromisoformat("1969-12-31T00:00:00+00:00"), 0),
        (datetime.fromisoformat("2026-01-01T00:00:00+00:00"), 1 << ULID_RANDOM_BITS),
    ],
)
def test_new_ulid_refuses_input_out_of_range(moment: datetime, randomness: int) -> None:
    """Naive or pre-epoch moments and oversized randomness are refused."""
    with pytest.raises(ValueError, match=r"ULID|aware|80 bits"):
        new_ulid(moment, randomness)
