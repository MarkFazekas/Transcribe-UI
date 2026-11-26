"""Flow tests of the worker pipeline (architecture §5.3): moto S3, scripted fake engine.

The fake engine runs in a real engine process (spawn), so crashes and hangs are real. Audio
objects hold fake-engine scripts; the decoder copies them instead of running ffmpeg.
"""

import errno
import itertools
import json
import logging
import shutil
import sys
import time
import wave
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
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
    RunStatus,
    TranscribeJob,
    TranscriptionMode,
)
from transcribe_contracts.reading import read_batch_summary, read_job_result, read_run_status
from transcribe_worker.engine.classes import Transcript
from transcribe_worker.engine.constants import FAKE_BROKEN_FLAG_ENV
from transcribe_worker.engine.host import EngineError, EngineProcess
from transcribe_worker.pipeline.audio import AudioCache, DiskFullError, decode_to_wav
from transcribe_worker.pipeline.config import R2Settings, WorkerConfig
from transcribe_worker.pipeline.constants import ENGINE_FACTORIES, EXIT_FAILED, EXIT_OK, LOG_FILE_NAME
from transcribe_worker.pipeline.logs import JsonFormatter
from transcribe_worker.pipeline.runner import WorkerPorts, run_worker
from transcribe_worker.pipeline.storage import ObjectStore, S3ObjectStore

BUCKET = "transcribe-test"
RUN_ID = "01JDA4B7C8D9E0F1G2H3J4K5M6"
EARLIER_RUN_ID = "01JDA4B7C8D9E0F1G2H3J4K5M7"
BATCH_A = "batch_20261126_143005"
BATCH_B = "batch_20261126_150000"
START = datetime.fromisoformat("2026-11-26T12:00:00+00:00")
ENGLISH = "en"
HUNGARIAN = "hu"
JSON_TYPE = "application/json"
OK = "ok"
FAILED = "failed"
ENGINE_START_TIMEOUT_S = 60.0
DEFAULT_JOB_TIMEOUT_S = 600.0
SHORT_JOB_TIMEOUT_S = 3.0
IDLE_DEATH_WAIT_S = 1.0
MAX_RUNTIME_S = 7200
AUDIO_DURATION_S = 5.0
FAKE_SECRET = "test-secret"  # noqa: S105 - a fake credential for the moto bucket
SPEECH = b"FAKE:say Hello world. This is a test."
HUNGARIAN_SPEECH = "FAKE:say Szia világ. Ez egy próba.".encode()
ITEM_IDS = (
    "01JDA3Q5Z8V9K2M4N6P8R0T2W1",
    "01JDA3Q5Z8V9K2M4N6P8R0T2W2",
    "01JDA3Q5Z8V9K2M4N6P8R0T2W3",
    "01JDA3Q5Z8V9K2M4N6P8R0T2W4",
)
SOURCE_RATE_HZ = 44100
DECODED_FORMAT = (1, 2, 16000)
CACHE_LIMIT_BYTES = 250
CACHED_OBJECT_BYTES = 100


class OutageError(ConnectionError):
    """Simulated R2 failure."""

    def __init__(self) -> None:
        """Create the error with a recognisable message."""
        super().__init__("R2 unreachable")


@dataclass(frozen=True)
class Source:
    """One test media item: its fake-engine script and the languages to transcribe."""

    script: bytes
    languages: tuple[str, ...] = (ENGLISH,)
    mode: TranscriptionMode = "normal"


SPEECH_SOURCE = Source(SPEECH)


@dataclass(frozen=True)
class FailingStore:
    """ObjectStore wrapper that fails chosen operations."""

    inner: ObjectStore
    fail_list: bool = False
    fail_new: str | None = None
    fail_download: bool = False

    def list_keys(self, prefix: str) -> list[str]:
        """List, or fail when asked to."""
        if self.fail_list:
            raise OutageError
        return self.inner.list_keys(prefix)

    def read(self, key: str) -> bytes | None:
        """Read from the wrapped store."""
        return self.inner.read(key)

    def download(self, key: str, target: Path) -> None:
        """Download from the wrapped store, or fail when asked to."""
        if self.fail_download:
            raise OutageError
        self.inner.download(key, target)

    def write(self, key: str, body: bytes, content_type: str) -> None:
        """Write to the wrapped store."""
        self.inner.write(key, body, content_type)

    def write_new(self, key: str, body: bytes, content_type: str) -> bool:
        """Write to the wrapped store, or fail for the chosen key."""
        if key == self.fail_new:
            raise OutageError
        return self.inner.write_new(key, body, content_type)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[S3ObjectStore]:
    """Return an S3ObjectStore on an empty moto bucket."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", FAKE_SECRET)
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield S3ObjectStore(client, BUCKET)


@pytest.fixture
def config(tmp_path: Path) -> WorkerConfig:
    """Return a worker configuration using folders under tmp_path and the fake engine."""
    r2 = R2Settings.model_validate(
        {
            "endpoint_url": "https://example.r2.cloudflarestorage.com",
            "bucket": BUCKET,
            "access_key_id": "id",
            "secret_access_key": FAKE_SECRET,
        },
    )
    return WorkerConfig(
        run_id=RUN_ID,
        pod_id="pod-test",
        max_runtime_s=MAX_RUNTIME_S,
        r2=r2,
        backup_dir=tmp_path / "backups",
        work_dir=tmp_path / "work",
        audio_cache_gb=1,
        engine_factory=ENGINE_FACTORIES["fake"],
        job_timeout_min_s=DEFAULT_JOB_TIMEOUT_S,
    )


def raise_outage() -> None:
    """Raise an OutageError (gives it a real traceback)."""
    raise OutageError


@pytest.fixture
def run_once(store: S3ObjectStore, config: WorkerConfig) -> Callable[[], int]:
    """Return a function running the worker once on the test bucket with the test config."""
    return partial(run, store, config)


def make_clock() -> Callable[[], datetime]:
    """Return a clock that advances one second per call."""
    ticks = itertools.count()
    return lambda: START + timedelta(seconds=next(ticks))


def copy_decode(source: Path, target: Path) -> None:
    """Decoder that keeps the fake-engine script."""
    shutil.copyfile(source, target)


def make_item(store: ObjectStore, batch_id: str, item_id: str, source: Source) -> ManifestItem:
    """Upload one item's audio and return its manifest entry."""
    key = layout.audio_key(batch_id, item_id, "opus", None)
    store.write(key, source.script, "audio/ogg")
    size = len(source.script)
    audio = AudioObject(
        object_key=key,
        track_index=None,
        extension="opus",
        codec="opus",
        duration_s=AUDIO_DURATION_S,
        size_bytes=size,
    )
    jobs = [TranscribeJob(language=language, mode=source.mode, audio_object_key=key) for language in source.languages]
    return ManifestItem(
        item_id=item_id,
        original_path=f"/media/{item_id}.opus",
        source_size=size,
        source_mtime_ns=0,
        media_kind="audio",
        audio=(audio,),
        jobs=tuple(jobs),
    )


def put_batch(store: ObjectStore, batch_id: str, sources: list[Source]) -> list[str]:
    """Upload the audio and the manifest (last) of a batch; return the item ids."""
    entries = [make_item(store, batch_id, item_id, source) for item_id, source in zip(ITEM_IDS, sources, strict=False)]
    manifest = Manifest(
        batch_id=batch_id, created_at=START, scan_root="/media", path_flavor="posix", items=tuple(entries)
    )
    store.write(layout.manifest_key(batch_id), manifest.model_dump_json().encode(), JSON_TYPE)
    return [entry.item_id for entry in entries]


def run(store: ObjectStore, config: WorkerConfig, decode: Callable[[Path, Path], None] = copy_decode) -> int:
    """Run the worker once with a fresh fake engine process."""
    engine = EngineProcess(config.engine_factory, ENGINE_START_TIMEOUT_S)
    ports = WorkerPorts(store=store, engine=engine, decode=decode, now=make_clock())
    return run_worker(config, ports)


def read_result(store: ObjectStore, batch_id: str, item_id: str, language: str = ENGLISH) -> JobResult:
    """Read a job result from the store."""
    key = layout.result_json_key(batch_id, item_id, language)
    body = store.read(key)
    assert body is not None, key
    return read_job_result(key, body)


def read_subtitle(store: ObjectStore, batch_id: str, item_id: str, language: str = ENGLISH) -> str:
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


def read_status(store: ObjectStore) -> RunStatus:
    """Read the run status of RUN_ID."""
    key = layout.run_status_key(RUN_ID)
    body = store.read(key)
    assert body is not None
    return read_run_status(key, body)


def test_jobs_run_language_by_language(run_once: Callable[[], int], store: S3ObjectStore) -> None:
    """Two batches, mixed languages: all done, en jobs before hu jobs, summaries and status."""
    first, second = put_batch(store, BATCH_A, [Source(SPEECH, (HUNGARIAN, ENGLISH)), SPEECH_SOURCE])
    third = put_batch(store, BATCH_B, [Source(HUNGARIAN_SPEECH, (HUNGARIAN,))])[0]

    assert run_once() == EXIT_OK

    job_results = [
        read_result(store, BATCH_A, first, HUNGARIAN),
        read_result(store, BATCH_A, first),
        read_result(store, BATCH_A, second),
        read_result(store, BATCH_B, third, HUNGARIAN),
    ]
    assert {(job.status, job.run_id, job.aligned) for job in job_results} == {(OK, RUN_ID, True)}
    starts = [(job.started_at, job.language) for job in job_results]
    assert [language for _, language in sorted(starts)] == [ENGLISH, ENGLISH, HUNGARIAN, HUNGARIAN]
    subtitles = (read_subtitle(store, BATCH_A, second), read_subtitle(store, BATCH_B, third, HUNGARIAN))
    counts = (summary_counts(store, BATCH_A), summary_counts(store, BATCH_B))
    assert ("Hello world." in subtitles[0], "Szia világ." in subtitles[1], counts) == (
        True,
        True,
        ((3, 0, 0), (1, 0, 0)),
    )
    status = read_status(store)
    assert (status.state, status.done_jobs, status.total_jobs) == ("finished", 4, 4)


def test_one_bad_file_does_not_stop_the_batch(run_once: Callable[[], int], store: S3ObjectStore) -> None:
    """Failure, silence and an engine crash are recorded; the following job still succeeds."""
    sources = [Source(b"FAKE:fail"), Source(b"FAKE:silence"), Source(b"FAKE:crash"), SPEECH_SOURCE]
    failing, silent, crashing, healthy = put_batch(store, BATCH_A, sources)

    assert run_once() == EXIT_OK

    failed = read_result(store, BATCH_A, failing)
    assert (failed.status, failed.cue_count, "scripted failure" in (failed.error or "")) == (FAILED, 0, True)
    assert (read_result(store, BATCH_A, silent).status, read_subtitle(store, BATCH_A, silent)) == ("no_speech", "")
    crashed = read_result(store, BATCH_A, crashing)
    assert (crashed.status, "died" in (crashed.error or "")) == (FAILED, True)
    assert (read_result(store, BATCH_A, healthy).status, summary_counts(store, BATCH_A)) == (OK, (1, 1, 2))


def test_hung_job_times_out_engine_restarts(store: S3ObjectStore, config: WorkerConfig) -> None:
    """The hung engine process is killed at the job timeout; the next job gets a new one."""
    hanging, healthy = put_batch(store, BATCH_A, [Source(b"FAKE:hang"), SPEECH_SOURCE])

    assert run(store, config.model_copy(update={"job_timeout_min_s": SHORT_JOB_TIMEOUT_S})) == EXIT_OK

    hung = read_result(store, BATCH_A, hanging)
    assert (hung.status, "timed out" in (hung.error or "")) == (FAILED, True)
    assert read_result(store, BATCH_A, healthy).status == OK


def test_suspicious_output_is_redone_carefully(run_once: Callable[[], int], store: S3ObjectStore) -> None:
    """A repetition loop triggers a careful re-run, whose settings are recorded."""
    looping = put_batch(store, BATCH_A, [Source(b"FAKE:loop")])[0]

    assert run_once() == EXIT_OK

    redone = read_result(store, BATCH_A, looping)
    assert (redone.status, redone.mode, redone.engine.compute_type) == (OK, "normal", "careful")
    assert "Recovered text." in read_subtitle(store, BATCH_A, looping)


def test_existing_results_are_kept(run_once: Callable[[], int], store: S3ObjectStore) -> None:
    """A restarted run continues: a job with a result is skipped, the rest finishes the batch."""
    done_before, pending = put_batch(store, BATCH_A, [SPEECH_SOURCE, SPEECH_SOURCE])
    earlier = JobResult(
        batch_id=BATCH_A,
        item_id=done_before,
        language=ENGLISH,
        mode="normal",
        run_id=EARLIER_RUN_ID,
        status="no_speech",
        aligned=True,
        cue_count=0,
        audio_duration_s=AUDIO_DURATION_S,
        started_at=START,
        finished_at=START,
        engine=EngineInfo(name="fake", version="1", model="fake", compute_type="normal", batch_size=1),
    )
    store.write(layout.result_srt_key(BATCH_A, done_before, ENGLISH), b"", "application/x-subrip")
    store.write(layout.result_json_key(BATCH_A, done_before, ENGLISH), earlier.model_dump_json().encode(), JSON_TYPE)

    assert run_once() == EXIT_OK

    assert read_result(store, BATCH_A, done_before).run_id == EARLIER_RUN_ID
    assert read_result(store, BATCH_A, pending).run_id == RUN_ID
    assert summary_counts(store, BATCH_A) == (1, 1, 0)


def test_failed_upload_is_finished_from_the_backup(store: S3ObjectStore, config: WorkerConfig) -> None:
    """Upload fails: the batch stays open; the next run uploads the backup without an engine."""
    lost, _ = put_batch(store, BATCH_A, [SPEECH_SOURCE, SPEECH_SOURCE])
    lost_key = layout.result_json_key(BATCH_A, lost, ENGLISH)

    assert run(FailingStore(store, fail_new=lost_key), config) == EXIT_FAILED

    backed_up = config.backup_dir.joinpath(*lost_key.split("/"))
    assert (store.read(lost_key), store.read(layout.done_key(BATCH_A)), backed_up.exists()) == (None, None, True)

    no_engine = {"run_id": EARLIER_RUN_ID, "engine_factory": "transcribe_worker.engine.missing:Nothing"}
    assert run(store, config.model_copy(update=no_engine)) == EXIT_OK

    restored = read_result(store, BATCH_A, lost)
    assert (restored.status, restored.run_id) == (OK, RUN_ID)
    assert ("Hello world." in read_subtitle(store, BATCH_A, lost), summary_counts(store, BATCH_A)) == (True, (2, 0, 0))


def test_done_and_unreadable_batches_are_skipped(run_once: Callable[[], int], store: S3ObjectStore) -> None:
    """A batch with `_done.json` and a manifest of another schema major are left alone."""
    put_batch(store, BATCH_A, [SPEECH_SOURCE])
    store.write(layout.done_key(BATCH_A), b"{}", JSON_TYPE)
    future = json.dumps({"schema_version": "2.0", "batch_id": BATCH_B}).encode()
    store.write(layout.manifest_key(BATCH_B), future, JSON_TYPE)

    assert run_once() == EXIT_OK

    assert store.list_keys(layout.results_prefix(BATCH_A)) == []
    assert store.list_keys(layout.batch_prefix(BATCH_B)) == []


def test_broken_audio_fails_only_its_job(store: S3ObjectStore, config: WorkerConfig) -> None:
    """The real ffmpeg decoder on a non-audio file: the job fails with ffmpeg's message."""
    broken_id = put_batch(store, BATCH_A, [Source(b"not audio at all")])[0]

    assert run(store, config, decode=decode_to_wav) == EXIT_OK

    broken = read_result(store, BATCH_A, broken_id)
    assert (broken.status, "ffmpeg failed" in (broken.error or "")) == (FAILED, True)
    assert summary_counts(store, BATCH_A) == (0, 0, 1)


def test_unreachable_storage_fails_the_run(store: S3ObjectStore, config: WorkerConfig) -> None:
    """Listing fails: exit code 1 and a failed run status."""
    assert run(FailingStore(store, fail_list=True), config) == EXIT_FAILED

    status = read_status(store)
    assert (status.state, "R2 unreachable" in (status.error or "")) == (FAILED, True)


def test_log_file_is_uploaded(run_once: Callable[[], int], store: S3ObjectStore, config: WorkerConfig) -> None:
    """The run log is uploaded next to the status."""
    log_file = config.work_dir / LOG_FILE_NAME
    log_file.parent.mkdir(parents=True)
    log_file.write_text('{"message": "hello"}\n')

    assert run_once() == EXIT_OK

    assert store.read(layout.run_log_key(RUN_ID)) == log_file.read_bytes()


def test_log_lines_are_json_with_traceback() -> None:
    """A log record with an exception becomes one JSON line holding the traceback."""
    try:
        raise_outage()
    except OutageError:
        record = logging.LogRecord("worker", logging.ERROR, __file__, 1, "upload %s failed", ("x",), sys.exc_info())
    line = JsonFormatter().format(record)

    entry = json.loads(line)
    assert (entry["level"], entry["message"], "\n" in line) == ("ERROR", "upload x failed", False)
    assert "OutageError: R2 unreachable" in entry["exception"]


def test_ffmpeg_decodes_to_mono_wav(tmp_path: Path) -> None:
    """A stereo 44.1 kHz file becomes 16 kHz mono 16-bit PCM."""
    source = tmp_path / "tone.wav"
    with wave.open(str(source), "wb") as stereo:
        stereo.setnchannels(2)
        stereo.setsampwidth(2)
        stereo.setframerate(SOURCE_RATE_HZ)
        stereo.writeframes(bytes(SOURCE_RATE_HZ * 4))
    target = tmp_path / "decoded.wav"

    decode_to_wav(source, target)

    with wave.open(str(target), "rb") as decoded:
        assert (decoded.getnchannels(), decoded.getsampwidth(), decoded.getframerate()) == DECODED_FORMAT


def test_audio_cache_evicts_least_recently_used(store: S3ObjectStore, tmp_path: Path) -> None:
    """Over the limit, the oldest unused file goes; the file just fetched stays."""
    for name in ("a", "b", "c"):
        store.write(name, bytes(CACHED_OBJECT_BYTES), "audio/ogg")
    cache = AudioCache(store, tmp_path / "cache", limit_bytes=CACHE_LIMIT_BYTES)

    first = cache.fetch("a")
    cache.fetch("b")
    cache.fetch("a")
    cache.fetch("c")

    assert first.exists()
    assert sorted(path.name for path in (tmp_path / "cache").iterdir()) == ["a", "c"]
    assert cache.used_bytes == 2 * CACHED_OBJECT_BYTES


def test_engine_that_cannot_restart_stops_the_run(
    run_once: Callable[[], int],
    store: S3ObjectStore,
    config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a crash the engine cannot start again: the run fails, later jobs stay open."""
    monkeypatch.setenv(FAKE_BROKEN_FLAG_ENV, str(config.work_dir / "broken"))
    breaking, pending = put_batch(store, BATCH_A, [Source(b"FAKE:break"), SPEECH_SOURCE])

    assert run_once() == EXIT_FAILED

    assert read_result(store, BATCH_A, breaking).status == FAILED
    pending_key = layout.result_json_key(BATCH_A, pending, ENGLISH)
    assert (store.read(pending_key), store.read(layout.done_key(BATCH_A))) == (None, None)
    assert "did not start" in (read_status(store).error or "")


def test_backups_are_restored_before_the_engine_starts(
    run_once: Callable[[], int],
    store: S3ObjectStore,
    config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backed-up result reaches R2 even when the engine cannot start for the other jobs."""
    lost = put_batch(store, BATCH_A, [SPEECH_SOURCE])[0]
    lost_key = layout.result_json_key(BATCH_A, lost, ENGLISH)
    assert run(FailingStore(store, fail_new=lost_key), config) == EXIT_FAILED
    put_batch(store, BATCH_B, [SPEECH_SOURCE])
    broken_flag = config.work_dir / "broken"
    broken_flag.touch()
    monkeypatch.setenv(FAKE_BROKEN_FLAG_ENV, str(broken_flag))

    assert run_once() == EXIT_FAILED

    assert (read_result(store, BATCH_A, lost).status, summary_counts(store, BATCH_A)) == (OK, (1, 0, 0))


def test_missing_audio_fails_but_outage_retries(
    run_once: Callable[[], int], store: S3ObjectStore, config: WorkerConfig
) -> None:
    """A missing audio object fails its job; a download outage leaves the job for the next run."""
    missing = put_batch(store, BATCH_A, [SPEECH_SOURCE])[0]
    put_batch(store, BATCH_B, [SPEECH_SOURCE])
    client = boto3.client("s3", region_name="us-east-1")
    client.delete_object(Bucket=BUCKET, Key=layout.audio_key(BATCH_A, missing, "opus", None))

    assert run(FailingStore(store, fail_download=True), config) == EXIT_FAILED
    no_results = [store.list_keys(layout.results_prefix(batch_id)) for batch_id in (BATCH_A, BATCH_B)]
    assert (no_results, "left for the next run" in (read_status(store).error or "")) == ([[], []], True)

    assert run_once() == EXIT_OK
    gone = read_result(store, BATCH_A, missing)
    assert (gone.status, "missing" in (gone.error or "")) == (FAILED, True)


def test_engine_gone_while_idle_fails_one_job(tmp_path: Path) -> None:
    """An engine killed between jobs fails the next job only; the one after gets a new engine."""
    script = tmp_path / "speech.wav"
    script.write_bytes(SPEECH)
    quitting = tmp_path / "quit.wav"
    quitting.write_bytes(b"FAKE:quit")
    engine = EngineProcess(ENGINE_FACTORIES["fake"], ENGINE_START_TIMEOUT_S)
    engine.transcribe(quitting, ENGLISH, careful=False, timeout_s=ENGINE_START_TIMEOUT_S)
    time.sleep(IDLE_DEATH_WAIT_S)
    with pytest.raises(EngineError, match=r"gone|died"):
        engine.transcribe(script, ENGLISH, careful=False, timeout_s=SHORT_JOB_TIMEOUT_S)

    transcript = engine.transcribe(script, ENGLISH, careful=False, timeout_s=ENGINE_START_TIMEOUT_S)
    engine.close()

    assert isinstance(transcript, Transcript)
    assert "Hello world." in transcript.text


def full_disk_decode(source: Path, target: Path) -> None:
    """Decoder that fails as ffmpeg does when the disk is full."""
    raise DiskFullError(errno.ENOSPC, f"{source} -> {target}: No space left on device")


def test_full_disk_leaves_jobs_for_the_next_run(store: S3ObjectStore, config: WorkerConfig) -> None:
    """A full disk is not the file's fault: no failed result, and the run reports failure."""
    put_batch(store, BATCH_A, [SPEECH_SOURCE])

    assert run(store, config, decode=full_disk_decode) == EXIT_FAILED

    assert (store.list_keys(layout.results_prefix(BATCH_A)), read_status(store).state) == ([], FAILED)
