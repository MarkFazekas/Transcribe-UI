"""Fixtures of the worker image smoke test: a fake Runpod API and the test batch."""

import json
import math
import struct
import threading
import wave
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from typing import TYPE_CHECKING, override

from transcribe_contracts import layout
from transcribe_contracts.classes import AudioObject, Manifest, ManifestItem, TranscribeJob

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client

POD_ID = "pod-smoke"
BUCKET = "smoke"
BATCH_ID = "batch_20261127_000000"
ITEM_ID = "01JDA3Q5Z8V9K2M4N6P8R0T2W1"
LANGUAGE = "en"
SAMPLE_RATE_HZ = 16000
TONE_HZ = 440
TONE_SECONDS = 2
AMPLITUDE = 8000
HTTP_OK = 200
HTTP_NO_CONTENT = 204
HTTP_NOT_FOUND = 404


class FakeRunpod(ThreadingHTTPServer):
    """Fake Runpod API: the pod exists until it is deleted."""

    def __init__(self) -> None:
        """Listen on a free localhost port."""
        super().__init__(("127.0.0.1", 0), FakeRunpodHandler)
        self.deleted = threading.Event()

    @property
    def base_url(self) -> str:
        """Return the API base URL."""
        return f"http://127.0.0.1:{self.server_address[1]}"


class FakeRunpodHandler(BaseHTTPRequestHandler):
    """GET returns the pod until DELETE; then 404."""

    server: FakeRunpod

    def do_GET(self) -> None:
        """Answer the pod probe."""
        if self.server.deleted.is_set():
            self._send(HTTP_NOT_FOUND, b"")
            return
        pod = {"id": POD_ID, "status": "RUNNING"}
        self._send(HTTP_OK, json.dumps(pod).encode())

    def do_DELETE(self) -> None:
        """Delete the pod."""
        self.server.deleted.set()
        self._send(HTTP_NO_CONTENT, b"")

    def do_POST(self) -> None:
        """Accept a stop request."""
        self._send(HTTP_OK, b"{}")

    @override
    def log_message(self, format_: str, *args: object) -> None:
        """Silence request logging."""

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def tone_wav() -> bytes:
    """Return a short mono 16 kHz sine tone as WAV bytes."""
    step = 2 * math.pi * TONE_HZ / SAMPLE_RATE_HZ
    count = SAMPLE_RATE_HZ * TONE_SECONDS
    samples = [_sample(step * index) for index in range(count)]
    buffer = BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE_HZ)
        frames = struct.pack(f"<{count}h", *samples)
        output.writeframes(frames)
    return buffer.getvalue()


def _sample(phase: float) -> int:
    return int(AMPLITUDE * math.sin(phase))


def upload_batch(client: "S3Client") -> None:
    """Create the bucket and upload one audio file and its manifest (last)."""
    audio = tone_wav()
    key = layout.audio_key(BATCH_ID, ITEM_ID, "wav", None)
    audio_object = AudioObject(
        object_key=key,
        track_index=None,
        extension="wav",
        codec="pcm_s16le",
        duration_s=TONE_SECONDS,
        size_bytes=len(audio),
    )
    entry = ManifestItem(
        item_id=ITEM_ID,
        original_path="/media/tone.wav",
        source_size=len(audio),
        source_mtime_ns=0,
        media_kind="audio",
        audio=(audio_object,),
        jobs=(TranscribeJob(language=LANGUAGE, mode="normal", audio_object_key=key),),
    )
    manifest = Manifest(
        batch_id=BATCH_ID,
        created_at=datetime.now(UTC),
        scan_root="/media",
        path_flavor="posix",
        items=(entry,),
    )
    client.create_bucket(Bucket=BUCKET)
    client.put_object(Bucket=BUCKET, Key=key, Body=audio)
    body = manifest.model_dump_json().encode()
    client.put_object(Bucket=BUCKET, Key=layout.manifest_key(BATCH_ID), Body=body)
