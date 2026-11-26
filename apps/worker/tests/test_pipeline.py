"""Flow tests of the worker pipeline (architecture §5.3): moto S3, scripted fake engine.

The fake engine runs in a real engine process (spawn), so crashes and hangs are real. Audio
objects hold fake-engine scripts; the decoder copies them instead of running ffmpeg.
"""

import itertools
import json
import shutil
import wave
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from transcribe_contracts import layout
from transcribe_contracts.classes import (
    AudioObject,
    EngineInfo,
    JobResult,
    Manifest,
    ManifestItem,
    TranscribeJob,
)
from transcribe_contracts.reading import read_batch_summary, read_job_result, read_run_status
from transcribe_worker.engine.host import EngineProcess
from transcribe_worker.pipeline.audio import AudioCache, decode_to_wav
from transcribe_worker.pipeline.config import R2Settings, WorkerConfig
from transcribe_worker.pipeline.constants import ENGINE_FACTORIES, EXIT_FAILED, EXIT_OK
from transcribe_worker.pipeline.runner import WorkerPorts, run_worker
from transcribe_worker.pipeline.storage import ObjectStore, S3ObjectStore

BUCKET = "transcribe-test"
RUN_ID = "01JDA4B7C8D9E0F1G2H3J4K5M6"
EARLIER_RUN_ID = "01JDA4B7C8D9E0F1G2H3J4K5M7"
BATCH_A = "batch_20261126_143005"
BATCH_B = "batch_20261126_150000"
START = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
ENGINE_START_TIMEOUT_S = 60.0
SHORT_JOB_TIMEOUT_S = 3.0
SPEECH = b"FAKE:say Hello world. This is a test."
HUNGARIAN_SPEECH = "FAKE:say Szia világ. Ez egy próba.".encode()
ULIDS = (
    "01JDA3Q5Z8V9K2M4N6P8R0T2W1",
    "01JDA3Q5Z8V9K2M4N6P8R0T2W2",
    "01JDA3Q5Z8V9K2M4N6P8R0T2W3",
    "01JDA3Q5Z8V9K2M4N6P8R0T2W4",
    "01JDA3Q5Z8V9K2M4N6P8R0T2W5",
)


@dataclass(frozen=True)
class Source:
    """One test media item: its fake-engine script and the languages to transcribe."""

    script: bytes
    languages: tuple[str, ...] = ("en",)
    mode: str = "normal"


class FailingStore:
    """ObjectStore wrapper that fails chosen operations, to simulate R2 outages."""

    def __init__(self, inner: ObjectStore, fail_list: bool = False, fail_new: str | None = None) -> None:
        self._inner = inner
        self._fail_list = fail_list
        self._fail_new = fail_new

    def list_keys(self, prefix: str) -> list[str]:
        if self._fail_list:
            raise ConnectionError("R2 unreachable")
        return self._inner.list_keys(prefix)

    def read(self, key: str) -> bytes | None:
        return self._inner.read(key)

    def download(self, key: str, target: Path) -> None:
        self._inner.download(key, target)

    def write(self, key: str, body: bytes, content_type: str) -> None:
        self._inner.write(key, body, content_type)

    def write_new(self, key: str, body: bytes, content_type: str) -> bool:
        if key == self._fail_new:
            raise ConnectionError("upload failed")
        return self._inner.write_new(key, body, content_type)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[S3ObjectStore]:
    """Return an S3ObjectStore on an empty moto bucket."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield S3ObjectStore(client, BUCKET)


def make_clock() -> Callable[[], datetime]:
    """Return a clock that advances one second per call (thread-safe enough under the GIL)."""
    ticks = itertools.count()
    return lambda: START + timedelta(seconds=next(ticks))


def make_config(tmp_path: Path, min_timeout_s: float = 600.0) -> WorkerConfig:
    """Return a worker configuration using folders under tmp_path and the fake engine."""
    r2 = R2Settings(
        endpoint_url="https://example.r2.cloudflarestorage.com",
        bucket=BUCKET,
        access_key_id="id",
        secret_access_key="secret",
    )
    return WorkerConfig(
        run_id=RUN_ID,
        pod_id="pod-test",
        max_runtime_s=7200,
        r2=r2,
        backup_dir=tmp_path / "backups",
        work_dir=tmp_path / "work",
        audio_cache_gb=1,
        engine_factory=ENGINE_FACTORIES["fake"],
        job_timeout_min_s=min_timeout_s,
    )


def copy_decode(source: Path, target: Path) -> None:
    """Decoder that keeps the fake-engine script."""
    shutil.copyfile(source, target)


def put_batch(store: ObjectStore, batch_id: str, sources: list[Source]) -> list[str]:
    """Upload the audio and the manifest (last) of a batch; return the item ids."""
    items = []
    for item_id, source in zip(ULIDS, sources, strict=False):
        key = layout.audio_key(batch_id, item_id, "opus", None)
        store.write(key, source.script, "audio/ogg")
        audio = AudioObject(object_key=key, track_index=None, extension="opus", codec="opus", duration_s=5, size_bytes=len(source.script))
        jobs = tuple(TranscribeJob(language=language, mode=source.mode, audio_object_key=key) for language in source.languages)
        items.append(
            ManifestItem(
                item_id=item_id,
                original_path=f"/media/{item_id}.opus",
                source_size=len(source.script),
                source_mtime_ns=0,
                media_kind="audio",
                audio=(audio,),
                jobs=jobs,
            ),
        )
    manifest = Manifest(batch_id=batch_id, created_at=START, scan_root="/media", path_flavor="posix", items=tuple(items))
    store.write(layout.manifest_key(batch_id), manifest.model_dump_json().encode(), "application/json")
    return [manifest_item.item_id for manifest_item in items]


def run(store: ObjectStore, config: WorkerConfig, decode: Callable[[Path, Path], None] = copy_decode) -> int:
    """Run the worker once with a fresh fake engine process."""
    engine = EngineProcess(config.engine_factory, ENGINE_START_TIMEOUT_S)
    ports = WorkerPorts(store=store, engine=engine, decode=decode, now=make_clock())
    return run_worker(config, ports)


def result(store: ObjectStore, batch_id: str, item_id: str, language: str = "en") -> JobResult:
    """Read a job result from the store."""
    key = layout.result_json_key(batch_id, item_id, language)
    body = store.read(key)
    assert body is not None, key
    return read_job_result(key, body)


def subtitle(store: ObjectStore, batch_id: str, item_id: str, language: str = "en") -> str:
    """Read a subtitle from the store."""
    body = store.read(layout.result_srt_key(batch_id, item_id, language))
    assert body is not None
    return body.decode()


def summary_counts(store: ObjectStore, batch_id: str) -> tuple[int, int, int]:
    """Return (ok, no_speech, failed) from the batch summary."""
    key = layout.done_key(batch_id)
    body = store.read(key)
    assert body is not None, key
    counts = read_batch_summary(key, body).counts
    return counts.ok, counts.no_speech, counts.failed


def test_every_job_is_transcribed_language_by_language(store: S3ObjectStore, tmp_path: Path) -> None:
    """Two batches, mixed languages: all done, en jobs before hu jobs, summaries and status."""
    items_a = put_batch(store, BATCH_A, [Source(SPEECH, ("hu", "en")), Source(SPEECH)])
    items_b = put_batch(store, BATCH_B, [Source(HUNGARIAN_SPEECH, ("hu",))])

    assert run(store, make_config(tmp_path)) == EXIT_OK

    results = [result(store, BATCH_A, items_a[0], "hu"), result(store, BATCH_A, items_a[0]), result(store, BATCH_A, items_a[1]), result(store, BATCH_B, items_b[0], "hu")]
    assert all(job.status == "ok" and job.run_id == RUN_ID and job.aligned for job in results)
    english_last_start = max(job.started_at for job in results if job.language == "en")
    hungarian_first_start = min(job.started_at for job in results if job.language == "hu")
    assert english_last_start < hungarian_first_start
    assert "Hello world." in subtitle(store, BATCH_A, items_a[1])
    assert "Szia világ." in subtitle(store, BATCH_B, items_b[0], "hu")
    assert summary_counts(store, BATCH_A) == (3, 0, 0)
    assert summary_counts(store, BATCH_B) == (1, 0, 0)
    status_key = layout.run_status_key(RUN_ID)
    status_body = store.read(status_key)
    assert status_body is not None
    status = read_run_status(status_key, status_body)
    assert (status.state, status.done_jobs, status.total_jobs) == ("finished", 4, 4)
    log = store.read(layout.run_log_key(RUN_ID))
    assert log is not None
    assert all(json.loads(line)["level"] for line in log.decode().splitlines())


def test_one_bad_file_does_not_stop_the_batch(store: S3ObjectStore, tmp_path: Path) -> None:
    """Failure, silence and an engine crash are recorded; the following jobs still succeed."""
    sources = [Source(b"FAKE:fail"), Source(b"FAKE:silence"), Source(b"FAKE:crash"), Source(SPEECH)]
    items = put_batch(store, BATCH_A, sources)

    assert run(store, make_config(tmp_path)) == EXIT_OK

    failed = result(store, BATCH_A, items[0])
    assert (failed.status, failed.cue_count) == ("failed", 0)
    assert "scripted failure" in (failed.error or "")
    assert result(store, BATCH_A, items[1]).status == "no_speech"
    assert subtitle(store, BATCH_A, items[1]) == ""
    crashed = result(store, BATCH_A, items[2])
    assert crashed.status == "failed"
    assert "died" in (crashed.error or "")
    assert result(store, BATCH_A, items[3]).status == "ok"
    assert summary_counts(store, BATCH_A) == (1, 1, 2)


def test_a_hanging_job_times_out_and_the_engine_restarts(store: S3ObjectStore, tmp_path: Path) -> None:
    """The hung engine process is killed at the job timeout; the next job gets a new one."""
    items = put_batch(store, BATCH_A, [Source(b"FAKE:hang"), Source(SPEECH)])

    assert run(store, make_config(tmp_path, min_timeout_s=SHORT_JOB_TIMEOUT_S)) == EXIT_OK

    hung = result(store, BATCH_A, items[0])
    assert hung.status == "failed"
    assert "timed out" in (hung.error or "")
    assert result(store, BATCH_A, items[1]).status == "ok"


def test_suspicious_output_is_redone_carefully(store: S3ObjectStore, tmp_path: Path) -> None:
    """A repetition loop triggers a careful re-run, whose settings are recorded."""
    items = put_batch(store, BATCH_A, [Source(b"FAKE:loop")])

    assert run(store, make_config(tmp_path)) == EXIT_OK

    redone = result(store, BATCH_A, items[0])
    assert (redone.status, redone.mode, redone.engine.compute_type) == ("ok", "normal", "careful")
    assert "Recovered text." in subtitle(store, BATCH_A, items[0])


def test_existing_results_are_kept_and_not_redone(store: S3ObjectStore, tmp_path: Path) -> None:
    """A restarted run continues: a job with a result is skipped, the rest finishes the batch."""
    items = put_batch(store, BATCH_A, [Source(SPEECH), Source(SPEECH)])
    earlier = JobResult(
        batch_id=BATCH_A,
        item_id=items[0],
        language="en",
        mode="normal",
        run_id=EARLIER_RUN_ID,
        status="no_speech",
        aligned=True,
        cue_count=0,
        audio_duration_s=5,
        started_at=START,
        finished_at=START,
        engine=EngineInfo(name="fake", version="1", model="fake", compute_type="normal", batch_size=1),
    )
    store.write(layout.result_srt_key(BATCH_A, items[0], "en"), b"", "application/x-subrip")
    store.write(layout.result_json_key(BATCH_A, items[0], "en"), earlier.model_dump_json().encode(), "application/json")

    assert run(store, make_config(tmp_path)) == EXIT_OK

    assert result(store, BATCH_A, items[0]).run_id == EARLIER_RUN_ID
    assert result(store, BATCH_A, items[1]).run_id == RUN_ID
    assert summary_counts(store, BATCH_A) == (1, 1, 0)


def test_failed_upload_is_completed_from_the_backup_next_run(store: S3ObjectStore, tmp_path: Path) -> None:
    """Upload fails: the batch stays open; the next run uploads the backup without transcribing."""
    items = put_batch(store, BATCH_A, [Source(SPEECH), Source(SPEECH)])
    lost_key = layout.result_json_key(BATCH_A, items[0], "en")
    config = make_config(tmp_path)

    assert run(FailingStore(store, fail_new=lost_key), config) == EXIT_OK

    assert store.read(lost_key) is None
    assert store.read(layout.done_key(BATCH_A)) is None
    backed_up = (tmp_path / "backups").joinpath(*lost_key.split("/"))
    assert backed_up.exists()

    next_config = config.model_copy(update={"run_id": EARLIER_RUN_ID, "engine_factory": "transcribe_worker.engine.missing:Nothing"})
    assert run(store, next_config) == EXIT_OK

    restored = result(store, BATCH_A, items[0])
    assert (restored.status, restored.run_id) == ("ok", RUN_ID)
    assert "Hello world." in subtitle(store, BATCH_A, items[0])
    assert summary_counts(store, BATCH_A) == (2, 0, 0)


def test_done_and_unreadable_batches_are_skipped(store: S3ObjectStore, tmp_path: Path) -> None:
    """A batch with `_done.json` and a manifest of another schema major are left alone."""
    put_batch(store, BATCH_A, [Source(SPEECH)])
    store.write(layout.done_key(BATCH_A), b"{}", "application/json")
    manifest_key = layout.manifest_key(BATCH_B)
    store.write(manifest_key, json.dumps({"schema_version": "2.0", "batch_id": BATCH_B}).encode(), "application/json")

    assert run(store, make_config(tmp_path)) == EXIT_OK

    assert store.list_keys(layout.results_prefix(BATCH_A)) == []
    assert store.list_keys(layout.batch_prefix(BATCH_B)) == []


def test_undecodable_audio_fails_only_its_job(store: S3ObjectStore, tmp_path: Path) -> None:
    """The real ffmpeg decoder on a non-audio file: the job fails with ffmpeg's message."""
    items = put_batch(store, BATCH_A, [Source(b"not audio at all")])

    assert run(store, make_config(tmp_path), decode=decode_to_wav) == EXIT_OK

    broken = result(store, BATCH_A, items[0])
    assert broken.status == "failed"
    assert "ffmpeg failed" in (broken.error or "")


def test_unreachable_storage_fails_the_run(store: S3ObjectStore, tmp_path: Path) -> None:
    """Listing fails: exit code 1 and a failed run status (written once storage answers)."""
    assert run(FailingStore(store, fail_list=True), make_config(tmp_path)) == EXIT_FAILED

    status_key = layout.run_status_key(RUN_ID)
    body = store.read(status_key)
    assert body is not None
    status = read_run_status(status_key, body)
    assert status.state == "failed"
    assert "R2 unreachable" in (status.error or "")


def test_ffmpeg_decodes_to_16k_mono(tmp_path: Path) -> None:
    """A stereo 44.1 kHz file becomes 16 kHz mono 16-bit PCM."""
    source = tmp_path / "tone.wav"
    with wave.open(str(source), "wb") as stereo:
        stereo.setnchannels(2)
        stereo.setsampwidth(2)
        stereo.setframerate(44100)
        stereo.writeframes(bytes(44100 * 4))
    target = tmp_path / "decoded.wav"

    decode_to_wav(source, target)

    with wave.open(str(target), "rb") as decoded:
        assert (decoded.getnchannels(), decoded.getsampwidth(), decoded.getframerate()) == (1, 2, 16000)


def test_audio_cache_evicts_the_least_recently_used(store: S3ObjectStore, tmp_path: Path) -> None:
    """Over the limit, the oldest unused file goes; a fetched file is never evicted."""
    for name in ("a", "b", "c"):
        store.write(name, bytes(100), "audio/ogg")
    cache = AudioCache(store, tmp_path / "cache", limit_bytes=250)

    first = cache.fetch("a")
    cache.fetch("b")
    cache.fetch("a")
    cache.fetch("c")

    assert first.exists()
    assert sorted(path.name for path in (tmp_path / "cache").iterdir()) == ["a", "c"]
    assert cache.used_bytes == 200
