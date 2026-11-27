"""Smoke test of the built worker image (CI, no GPU): one real run with the fake engine.

Usage: `uv run scripts/smoke_worker_image.py <image>`

Starts an S3 server (moto, in Docker) and a fake Runpod API on localhost, uploads one batch
with a short tone, and runs the image as a pod would (entry → worker → self-termination) with
`TRANSCRIBE_ENGINE=fake`. Checks: the job result and `_done.json` exist, the run status is
"finished", the pod was deleted through the API, and the container exited 0. Then checks
that the GPU stack imports and the baked models are in the image.
"""

import argparse
import shutil
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from typing import TYPE_CHECKING
from urllib.request import urlopen

import boto3
from smoke_fixtures import BATCH_ID, BUCKET, ITEM_ID, LANGUAGE, POD_ID, FakeRunpod, upload_batch

from transcribe_contracts import layout
from transcribe_contracts.reading import read_batch_summary, read_job_result, read_run_status

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client

MOTO_IMAGE = "motoserver/moto:5.2.6@sha256:344e076f31ad1f5ff1ec4492f741e7d42911d1c7a1ef9b1590f56c340b968513"
MOTO_CONTAINER = "transcribe-smoke-moto"
S3_PORT = 5000
S3_URL = f"http://127.0.0.1:{S3_PORT}"
RUN_ID = "01JDA4B7C8D9E0F1G2H3J4K5M6"
RUN_TIMEOUT_S = 900
STARTUP_TIMEOUT_S = 60
POLL_S = 0.5
THROWAWAY_CREDENTIAL = "smoke"
# Files the image must contain (model caches baked at build time).
BAKED_PATHS = (
    "/opt/models/huggingface/hub/models--Systran--faster-whisper-large-v3",
    "/opt/models/huggingface/hub/models--jonatasgrosman--wav2vec2-large-xlsr-53-hungarian",
    "/opt/models/torch/hub/snakers4_silero-vad_master",
    "/opt/models/torch/hub/checkpoints",
)
# Python run in the image: the GPU stack imports without a GPU.
IMPORT_CHECK = "import transcribe_worker.engine.whisperx_engine, transcribe_worker.engine.bake"


class SmokeError(AssertionError):
    """A smoke check failed."""


def docker(*args: str, timeout_s: float | None = None) -> int:
    """Run a docker command and return its exit code."""
    executable = shutil.which("docker")
    if executable is None:
        msg = "docker is not installed"
        raise SmokeError(msg)
    return subprocess.run([executable, *args], timeout=timeout_s, check=False).returncode


def s3_client() -> "S3Client":
    """Return a boto3 S3 client for the local moto server."""
    return boto3.client(
        "s3",
        endpoint_url=S3_URL,
        region_name="us-east-1",
        aws_access_key_id=THROWAWAY_CREDENTIAL,
        aws_secret_access_key=THROWAWAY_CREDENTIAL,
    )


def run_worker(image: str, api: FakeRunpod) -> int:
    """Run the image as a pod would and return the container's exit code."""
    env = {
        "RUNPOD_POD_ID": POD_ID,
        "RUNPOD_API_KEY": THROWAWAY_CREDENTIAL,
        "RUNPOD_API_BASE_URL": api.base_url,
        "MAX_RUNTIME_S": str(RUN_TIMEOUT_S),
        "TRANSCRIBE_RUN_ID": RUN_ID,
        "TRANSCRIBE_ENGINE": "fake",
        "TRANSCRIBE_BACKUP_DIR": "/tmp/backups",  # noqa: S108 - inside the throwaway container
        "R2_ENDPOINT_URL": S3_URL,
        "R2_BUCKET": BUCKET,
        "R2_ACCESS_KEY_ID": THROWAWAY_CREDENTIAL,
        "R2_SECRET_ACCESS_KEY": THROWAWAY_CREDENTIAL,
    }
    env_args: list[str] = []
    for name, setting in env.items():
        env_args.extend(("--env", f"{name}={setting}"))
    return docker("run", "--rm", "--network", "host", *env_args, image, timeout_s=RUN_TIMEOUT_S)


def read_object(client: "S3Client", key: str) -> bytes:
    """Return an object's body."""
    body: bytes = client.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    return body


def check_outputs(client: "S3Client", api: FakeRunpod, exit_code: int) -> None:
    """Check what the run left behind.

    Raises:
        SmokeError: A check failed.
    """
    result_key = layout.result_json_key(BATCH_ID, ITEM_ID, LANGUAGE)
    done_key = layout.done_key(BATCH_ID)
    status_key = layout.run_status_key(RUN_ID)
    job = read_job_result(result_key, read_object(client, result_key))
    done = read_batch_summary(done_key, read_object(client, done_key))
    status = read_run_status(status_key, read_object(client, status_key))
    deleted = api.deleted.is_set()
    found = (exit_code, job.status, done.counts.ok, status.state, deleted)
    expected = (0, "ok", 1, "finished", True)
    if found != expected:
        msg = f"(exit code, job status, ok count, run state, pod deleted) = {found}, expected {expected}"
        raise SmokeError(msg)


def check_image_contents(image: str) -> None:
    """Check that the GPU stack imports and the baked models exist.

    Raises:
        SmokeError: A check failed.
    """
    imports = docker("run", "--rm", "--entrypoint", "python", image, "-c", IMPORT_CHECK)
    listing = docker("run", "--rm", "--entrypoint", "ls", image, "-d", *BAKED_PATHS)
    if imports or listing:
        msg = f"image contents: imports exit {imports}, baked models exit {listing}"
        raise SmokeError(msg)


def wait_for_s3() -> None:
    """Wait until the moto server answers.

    Raises:
        SmokeError: It did not start in time.
    """
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if _answers(S3_URL):
            return
        time.sleep(POLL_S)
    msg = "the moto server did not start"
    raise SmokeError(msg)


def _answers(url: str) -> bool:
    try:
        urlopen(url, timeout=POLL_S).close()  # noqa: S310 - fixed local URL
    except OSError:
        return False
    return True


def smoke(image: str) -> None:
    """Run every smoke check against the image."""
    with ExitStack() as cleanup:
        if docker("run", "-d", "--rm", "--name", MOTO_CONTAINER, "-p", f"127.0.0.1:{S3_PORT}:5000", MOTO_IMAGE):
            msg = "could not start the moto server"
            raise SmokeError(msg)
        cleanup.callback(docker, "stop", MOTO_CONTAINER)
        api = FakeRunpod()
        threading.Thread(target=api.serve_forever, daemon=True).start()
        cleanup.callback(api.shutdown)
        wait_for_s3()
        client = s3_client()
        upload_batch(client)
        check_outputs(client, api, run_worker(image, api))
    check_image_contents(image)


def main() -> int:
    """Parse the arguments and run the smoke test."""
    parser = argparse.ArgumentParser(description="Smoke test of the built worker image.")
    parser.add_argument("image", help="image to test, e.g. transcribe-ui-worker:smoke")
    smoke(parser.parse_args().image)
    sys.stdout.write("worker image smoke test passed\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
