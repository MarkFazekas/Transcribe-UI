"""Launch, cancel and reconcile helpers over moto and the fake Runpod API (plan m5 M5.11).

Batches are seeded through the state store (the upload is M5.10): a held batch has its audio in
the bucket, a ready one also its manifest. Parity legs each get their own bucket and fake API.
"""

import json
from datetime import datetime
from typing import Any

from pydantic import JsonValue

from support.batch_backend import run
from support.runs import JSON_TYPE, RunsWorld, seed_uploaded
from transcribe_contracts.classes import AudioObject, Manifest, ManifestItem, TranscribeJob
from transcribe_contracts.layout import audio_key, manifest_key
from transcribe_ui.api.classes import ErrorResponse, Response
from transcribe_ui.runpod.classes import SetupCloudRequest
from transcribe_ui.runs.classes import LaunchPlan
from transcribe_ui.runs.payloads import RunPayload
from transcribe_ui.runs.responses import LaunchWorkerResponse, PlanLaunchResponse, RunInfo
from transcribe_ui.state.models import RunRow
from transcribe_ui.storage.r2 import R2ObjectStore

# A response document as a dict.
type Document = dict[str, Any]

# A batch of another computer: its manifest is in R2, this database does not know it.
FOREIGN_BATCH_ID = "batch_20260101_090000"
# Item id of the foreign batch's single item.
FOREIGN_ITEM = "01JDZZZZZZZZZZZZZZZZZZZZ09"
# Audio duration of the foreign item.
FOREIGN_SECONDS = 1800.0
# Creation time of the foreign manifest.
FOREIGN_CREATED = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
# Path of the pod list and the pod create.
PODS = "/v2/pods"
# Path of the queued-pod list.
QUEUED_PODS = "/v2/queued-pods"
# Path of the GPU catalog.
CATALOG = "/v2/catalog/gpus"
# Method of the pod create.
POST = "POST"
# Request actions.
PLAN = "plan_launch"
LAUNCH = "launch_worker"
CANCEL = "cancel_worker"
RECONCILE = "reconcile_cloud"
TERMINATE = "terminate_pod"
CANCEL_QUEUED = "cancel_queued_pod"


def document(action: str, **fields: object) -> str:
    """Return a request document."""
    return json.dumps({"action": action, **fields})


def send(world: RunsWorld, action: str, **fields: object) -> Response:
    """Send a request to the world's backend."""
    return run(world.leg, document(action, **fields))


def response_document(response: Response) -> Document:
    """Return the response as a dict."""
    found: Document = json.loads(response.model_dump_json())
    return found


def set_up_cloud(world: RunsWorld) -> None:
    """Create the template, secrets and Global Volume in the world's fake Runpod API."""
    response = world.leg.backend.execute(SetupCloudRequest(action="setup_cloud"))
    assert response.status == "ok", response


def seed_ready(world: RunsWorld) -> dict[str, str]:
    """Seed the batch uploaded and published (`ready`, manifest in R2); return file name -> item id."""
    ids = seed_uploaded(world)
    assert world.step().outcome == "published"
    return ids


def foreign_manifest(batch_id: str = FOREIGN_BATCH_ID) -> Manifest:
    """Return the manifest of another computer's batch: one audio file, one `normal` German job."""
    key = audio_key(batch_id, FOREIGN_ITEM, "flac", None)
    audio = AudioObject(
        object_key=key, track_index=None, extension="flac", codec="flac", duration_s=FOREIGN_SECONDS, size_bytes=1
    )
    job = TranscribeJob(language="de", mode="normal", audio_object_key=key)
    entry = ManifestItem(
        item_id=FOREIGN_ITEM,
        original_path="/elsewhere/talk.flac",
        source_size=1,
        source_mtime_ns=0,
        media_kind="audio",
        audio=(audio,),
        jobs=(job,),
    )
    return Manifest(
        batch_id=batch_id, created_at=FOREIGN_CREATED, scan_root="/elsewhere", path_flavor="posix", items=(entry,)
    )


def write_foreign(store: R2ObjectStore, batch_id: str = FOREIGN_BATCH_ID) -> None:
    """Publish another computer's manifest."""
    store.write(manifest_key(batch_id), foreign_manifest(batch_id).model_dump_json().encode(), JSON_TYPE)


def plan_fingerprint(world: RunsWorld, **fields: object) -> str:
    """Send `plan_launch`; return the plan's fingerprint."""
    planned = send(world, PLAN, **fields)
    assert planned.status == "ok", planned
    fingerprint: str = response_document(planned)["plan"]["scope"]["fingerprint"]
    return fingerprint


def launch_confirmed(world: RunsWorld, **fields: object) -> Response:
    """Plan, then send `launch_worker` confirming that plan."""
    test_mode = fields.get("test_mode", False)
    fingerprint = plan_fingerprint(world, test_mode=test_mode)
    return send(world, LAUNCH, confirm_fingerprint=fingerprint, **fields)


def launched_run(response: Response) -> Document:
    """Return the run of a successful `launch_worker` response."""
    assert response.status == "ok", response
    found: Document = response_document(response)["run"]
    return found


def run_row(world: RunsWorld, run_id: str) -> tuple[str, RunPayload]:
    """Return the run's status and payload."""
    with world.ports.state.session() as session:
        row = session.get(RunRow, run_id)
        assert row is not None
        return row.status, RunPayload.model_validate(row.payload)


def error_of(response: Response) -> tuple[str, dict[str, JsonValue]]:
    """Return the code and details of an error response."""
    assert isinstance(response, ErrorResponse), response
    return response.error.code, dict(response.error.details)


def error_code(response: Response) -> str:
    """Return the code of an error response."""
    return error_of(response)[0]


def reconcile(world: RunsWorld) -> Document:
    """Send `reconcile_cloud`; return the response document."""
    reconciled = send(world, RECONCILE)
    assert reconciled.status == "ok", reconciled
    return response_document(reconciled)


def run_ids(world: RunsWorld) -> list[str]:
    """Return every local run id."""
    with world.ports.state.session() as session:
        return [row.run_id for row in session.query(RunRow).order_by(RunRow.run_id)]


def ok_as[ResponseT](response: Response, kind: type[ResponseT]) -> ResponseT:
    """Return the response as its success model."""
    assert isinstance(response, kind), response
    return response


def plan_of(world: RunsWorld, **fields: object) -> LaunchPlan:
    """Send `plan_launch`; return the plan."""
    return ok_as(send(world, PLAN, **fields), PlanLaunchResponse).plan


def launch(world: RunsWorld, **fields: object) -> LaunchWorkerResponse:
    """Plan, then launch confirming that plan; the launch must succeed."""
    return ok_as(launch_confirmed(world, **fields), LaunchWorkerResponse)


def launch_run(world: RunsWorld, **fields: object) -> RunInfo:
    """Launch (confirmed); return the run."""
    return launch(world, **fields).run


def run_status(world: RunsWorld, run_id: str) -> str:
    """Return the run's status."""
    return run_row(world, run_id)[0]


def pods_of(world: RunsWorld) -> list[tuple[str, str]]:
    """Return the fake's listed pods as (id, status), newest first."""
    return [(str(pod["id"]), str(pod["status"])) for pod in world.cloud.api.pods.listed()]


def queued_of(world: RunsWorld) -> list[tuple[str, str]]:
    """Return the fake's listed queued pods as (id, status), newest first."""
    return [(str(entry["id"]), str(entry["status"])) for entry in world.cloud.api.queued_pods.listed()]


def posts(world: RunsWorld) -> int:
    """Return how many pod requests reached the fake."""
    return world.cloud.api.count(POST, PODS)


def pod_env(world: RunsWorld) -> dict[str, str]:
    """Return the env of the first pod request that reached the fake."""
    bodies = world.cloud.api.pods.create_bodies
    env: dict[str, str] = bodies[0]["env"]
    return env
