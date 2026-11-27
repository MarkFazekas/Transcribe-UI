"""The launch scope (plan m5 M5.9): pending manifests in R2 as the worker sees them, plus held ones."""

import json
from datetime import datetime

from support.batches import FILM, FIRST_BATCH_ID, PODCAST
from support.runs import JSON_TYPE, RunsWorld, seed_uploaded
from transcribe_contracts.classes import AudioObject, Manifest, ManifestItem, TranscribeJob
from transcribe_contracts.layout import audio_key, done_key, manifest_key, result_json_key
from transcribe_ui.runs.classes import ScopeBatch
from transcribe_ui.runs.scope import scope_fingerprint

# A batch of another computer: its manifest is in R2, this database does not know it.
UNKNOWN_BATCH_ID = "batch_20260101_090000"
# A batch written by an app of a newer contract major.
NEWER_BATCH_ID = "batch_20260102_090000"
# A batch whose manifest is not valid JSON.
BROKEN_BATCH_ID = "batch_20260103_090000"
# An object under `batch_metadata/` that is not a manifest.
STRAY_KEY = "batch_metadata/readme.txt"
# The seeded batch's files.
EVERY_SEEDED = (FILM, PODCAST)
# A finished batch: it has `_done.json`.
DONE_BATCH_ID = "batch_20260104_090000"
# Item id of the foreign batches' single item.
FOREIGN_ITEM = "01JDZZZZZZZZZZZZZZZZZZZZ09"
# Audio duration of the foreign batches' item.
FOREIGN_SECONDS = 120.5
# Creation time of the foreign manifests.
FOREIGN_CREATED = "2026-01-01T00:00:00+00:00"
# Languages of the foreign item's jobs.
FOREIGN_LANGUAGES = ("de", "fr")


def _foreign_manifest(batch_id: str) -> Manifest:
    key = audio_key(batch_id, FOREIGN_ITEM, "flac", None)
    audio = AudioObject(
        object_key=key, track_index=None, extension="flac", codec="flac", duration_s=FOREIGN_SECONDS, size_bytes=1
    )
    jobs = tuple(
        TranscribeJob(language=language, mode="careful", audio_object_key=key) for language in FOREIGN_LANGUAGES
    )
    entry = ManifestItem(
        item_id=FOREIGN_ITEM,
        original_path="/elsewhere/talk.flac",
        source_size=1,
        source_mtime_ns=0,
        media_kind="audio",
        audio=(audio,),
        jobs=jobs,
    )
    created = datetime.fromisoformat(FOREIGN_CREATED)
    return Manifest(batch_id=batch_id, created_at=created, scan_root="/elsewhere", path_flavor="posix", items=(entry,))


def _write(world: RunsWorld, key: str, body: bytes) -> None:
    world.store.write(key, body, JSON_TYPE)


def _seed_cloud(world: RunsWorld) -> dict[str, str]:
    """Publish the seeded batch with the film done, plus foreign, newer, broken, stray and finished objects."""
    ids = seed_uploaded(world)
    assert world.step().outcome == "published"
    _write(world, result_json_key(FIRST_BATCH_ID, ids[FILM], "en"), b"{}")
    _write(world, manifest_key(UNKNOWN_BATCH_ID), _foreign_manifest(UNKNOWN_BATCH_ID).model_dump_json().encode())
    newer = json.loads(_foreign_manifest(NEWER_BATCH_ID).model_dump_json()) | {"schema_version": "2.0"}
    _write(world, manifest_key(NEWER_BATCH_ID), json.dumps(newer).encode())
    _write(world, manifest_key(BROKEN_BATCH_ID), b"{broken")
    _write(world, STRAY_KEY, b"stray")
    _write(world, manifest_key(DONE_BATCH_ID), b"{also broken, but finished}")
    _write(world, done_key(DONE_BATCH_ID), b"{}")
    return ids


def test_scope_counts_unfinished_manifests(runs_world: RunsWorld) -> None:
    """Known and unknown manifests count by their jobs without a result; unreadable ones are listed only."""
    ids = _seed_cloud(runs_world)
    scope = runs_world.scope()
    unknown, known = scope.batches
    known_jobs = [(job.item_id, job.language) for job in known.pending_jobs]
    unknown_jobs = [(job.language, job.mode) for job in unknown.pending_jobs]
    unreadable = [(entry.key, entry.reason) for entry in scope.unreadable]
    assert (known.batch_id, known.known, known.held, known.files, known_jobs) == (
        FIRST_BATCH_ID,
        True,
        False,
        len(EVERY_SEEDED),
        [(ids[PODCAST], "en")],
    )
    assert (unknown.batch_id, unknown.known, unknown.files, unknown.pending_duration_s, unknown_jobs) == (
        UNKNOWN_BATCH_ID,
        False,
        1,
        FOREIGN_SECONDS * len(FOREIGN_LANGUAGES),
        [(language, "careful") for language in FOREIGN_LANGUAGES],
    )
    assert unreadable == [
        (manifest_key(NEWER_BATCH_ID), "unsupported_schema"),
        (manifest_key(BROKEN_BATCH_ID), "invalid"),
        (STRAY_KEY, "not_a_manifest"),
    ]
    totals = (scope.pending_jobs, scope.pending_duration_s, scope.fingerprint)
    expected_duration = known.pending_duration_s + unknown.pending_duration_s
    assert totals == (
        len(known_jobs) + len(unknown_jobs),
        expected_duration,
        scope_fingerprint(reversed(scope.batches)),
    )


def test_held_batches_count_with_the_same_fingerprint(runs_world: RunsWorld) -> None:
    """A held manifest counts with every job pending; once published, the scope (and fingerprint) is the same."""
    seed_uploaded(runs_world, held=True)
    held = runs_world.scope()
    batch = held.batches[0]
    assert (len(held.batches), batch.batch_id, batch.known, batch.held, len(batch.pending_jobs)) == (
        1,
        FIRST_BATCH_ID,
        True,
        True,
        len(EVERY_SEEDED),
    )
    assert runs_world.publish_held().published == (FIRST_BATCH_ID,)
    published = runs_world.scope()
    assert published.batches[0].held is False
    assert published.fingerprint == held.fingerprint


def test_fingerprint_follows_the_scope() -> None:
    """A new result, another batch or another duration changes the fingerprint; the order does not."""
    job = {"item_id": FOREIGN_ITEM, "language": "de", "mode": "normal", "duration_s": FOREIGN_SECONDS}
    base = ScopeBatch.model_validate(
        {
            "batch_id": UNKNOWN_BATCH_ID,
            "known": False,
            "held": False,
            "files": 1,
            "pending_jobs": [job],
            "pending_duration_s": FOREIGN_SECONDS,
        }
    )
    other = base.model_copy(update={"batch_id": NEWER_BATCH_ID})
    longer = base.model_copy(update={"pending_jobs": (base.pending_jobs[0].model_copy(update={"duration_s": 1.0}),)})
    finished = base.model_copy(update={"pending_jobs": ()})
    scopes = ([base], [base, other], [longer], [finished])
    prints = {scope_fingerprint(batches) for batches in scopes}
    assert len(prints) == len(scopes)
    assert scope_fingerprint([base, other]) == scope_fingerprint([other, base])
