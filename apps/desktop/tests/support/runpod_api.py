"""Stateful fake of the Runpod REST v2 API, served through `httpx.MockTransport`.

No request leaves the process. The fake keeps pods, queued pods, templates, secrets and Global
Volumes, and can inject what the client must survive: an HTTP status, a failure to connect, a
response lost after the request took effect, a redirect; objects that show up in lists only
after a delay; queued pods that deploy on a schedule; terminal entries kept in the lists; no
capacity. Its clock is the test's (`now`), so nothing sleeps.
"""

import json
import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from support.credentials import TEST_RUNPOD_KEY
from transcribe_ui.runpod.constants import (
    HTTP_ACCEPTED,
    HTTP_CLIENT_ERROR,
    HTTP_CONFLICT,
    HTTP_NOT_FOUND,
    HTTP_UNAUTHORIZED,
    POD_TERMINATED,
    QUEUED_POD_CANCELLED,
    QUEUED_POD_DEPLOYED,
    QUEUED_POD_EXPIRED,
    QUEUED_POD_QUEUED,
)

# Host the fake answers for (the real API host; nothing leaves the process).
FAKE_API_HOST = "api.runpod.io"
# Path prefix of every API path (the base URL's path).
FAKE_API_PREFIX = "/v2/"
# The key the fake accepts unless a test sets another (the credential tests' sentinel key).
FAKE_API_KEY = TEST_RUNPOD_KEY
# GPU type the fake catalog lists.
FAKE_GPU_ID = "NVIDIA RTX A5000"
# Secure-cloud price of the fake catalog's GPU (dollars per hour).
FAKE_GPU_PRICE = 0.27
# Status a pod gets when the fake creates it.
FAKE_POD_START_STATUS = "RUNNING"
# Multiplier scrambling the fake's ids (prime, sharing no factor with `ID_SPACE`: ids stay unique).
ID_STRIDE = 7919
# Number of distinct ids per collection.
ID_SPACE = 10000
# Fields `POST /v2/templates` accepts (Runpod refuses unknown ones with 422).
TEMPLATE_FIELDS = frozenset(
    (
        "name",
        "image",
        "args",
        "entrypoint",
        "cmd",
        "disk",
        "env",
        "ports",
        "registry",
        "category",
        "allowedCudaVersions",
        "mounts",
        "public",
        "serverless",
        "startJupyter",
        "startSsh",
    ),
)
# Fields `POST /v2/account/secrets` accepts.
SECRET_FIELDS = frozenset(("name", "value", "description"))
# Valid Runpod secret names (Runpod also refuses names starting with RUNPOD, in any case).
VAULT_NAME_PATTERN = r"[a-zA-Z_][a-zA-Z0-9_.\-/]{0,190}"
# Prefix Runpod reserves for its own Runpod secret names.
RESERVED_VAULT_PREFIX = "runpod"
# Status of a validation failure.
VALIDATION_FAILED = 422
# Status field of pods and queued pods.
STATUS = "status"
# Name field of every object.
NAME = "name"
# Env field of pods and templates.
ENV = "env"
# Not-found detail of the fake's problem responses.
NOT_FOUND = "not found"

# A JSON object as the fake stores and serves it.
type Json = dict[str, Any]
# The current time (the test's clock).
type Now = Callable[[], datetime]


def iso(moment: datetime) -> str:
    """Return the moment as the API writes it (UTC, `Z`)."""
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def problem(status: int, detail: str) -> httpx.Response:
    """Return an `application/problem+json` error response."""
    body = {"title": "error", STATUS: status, "detail": detail}
    return httpx.Response(status, json=body, headers={"Content-Type": "application/problem+json"})


@dataclass(frozen=True)
class Failure:
    """What the fake does to one matching request instead of (or after) carrying it out."""

    # Answer this status and change nothing.
    status: int | None = None
    retry_after: str | None = None
    # Raise `httpx.ConnectError`: nothing was sent, nothing changed.
    connect_error: bool = False
    # Carry the request out, then raise `httpx.ReadTimeout` (the response is lost).
    lost_response: bool = False
    # Answer a redirect to this URL and change nothing.
    redirect_to: str | None = None
    # Carry the request out, then answer its status with a body of an unexpected shape.
    garbled_body: bool = False
    # Carry the request out, then answer with this `status` in the body instead.
    status_in_body: str | None = None


@dataclass
class FakeSettings:
    """Behaviour of the fake; tests may change it between requests."""

    # How long a new object stays out of the lists (reads by id see it at once).
    list_lag: timedelta = timedelta(0)
    page_size: int = 1000
    # False: a create without `queue` answers 400, one with `queue` gives a queued pod.
    capacity: bool = True
    # When a queued pod deploys after its creation; None: never.
    deploy_after: timedelta | None = None
    gpu_price: float = FAKE_GPU_PRICE
    # False (the documented API): a terminated pod no longer appears in the list and reads 404;
    # True: it stays listed and readable with status TERMINATED.
    keep_terminal: bool = False
    # False: a pod DELETE answers 204 but the pod keeps running (a terminate that did not take effect).
    terminate_effective: bool = True
    # Called with each request before it is answered (tests pause a request here).
    on_request: Callable[[httpx.Request], None] | None = None


@dataclass(frozen=True)
class SeenRequest:
    """A request the fake received."""

    method: str
    path: str
    query: str
    authorised: bool
    # The client's connect and read timeouts for this request (seconds).
    timeouts: tuple[float | None, float | None] = (None, None)


@dataclass
class Record:
    """One stored object and when it shows up in lists."""

    body: Json
    listed_from: datetime
    # Queued pods: when the scheduler deploys it, and the create body it then starts.
    deploy_at: datetime | None = None
    request: Json | None = None


class Collection:
    """Objects of one API collection; subclasses add their create, update and delete rules."""

    def __init__(self, api: "FakeRunpodApi", list_key: str, prefix: str, *, newest_first: bool = False) -> None:
        """Keep the objects under `list_key` in list responses, ids starting with `prefix`."""
        self.api = api
        self.list_key = list_key
        self.records: dict[str, Record] = {}
        self._prefix = prefix
        self._newest_first = newest_first
        # Objects created so far; ids are never reused, even after a delete.
        self._created = 0

    def insert(self, body: Json) -> Json:
        """Store a new object with a fresh id and `createdAt`; return it."""
        now = self.api.now()
        self._created += 1
        # Not monotonic, like the real ids: the lowest id is not the oldest object.
        object_id = f"{self._prefix}{self._created * ID_STRIDE % ID_SPACE:04d}"
        stored = {"id": object_id, **body, "createdAt": iso(now)}
        self.records[object_id] = Record(stored, now + self.api.settings.list_lag)
        return stored

    def listed(self) -> list[Json]:
        """Return the objects the list shows now."""
        now = self.api.now()
        shown = [record.body for record in self.records.values() if record.listed_from <= now]
        return list(reversed(shown)) if self._newest_first else shown

    def find(self, object_id: str) -> Json | None:
        """Return the object, or None."""
        record = self.records.get(object_id)
        return None if record is None else record.body

    def page(self, query: httpx.QueryParams) -> httpx.Response:
        """Return one page of the list (`cursor` = offset, `limit` capped by the page size)."""
        listed = self.listed()
        page_size = self.api.settings.page_size
        start = int(query.get("cursor", "0"))
        end = start + min(int(query.get("limit", str(page_size))), page_size)
        more = end < len(listed)
        pagination = {"nextCursor": str(end) if more else None, "hasNextPage": more}
        return httpx.Response(httpx.codes.OK, json={self.list_key: listed[start:end], "pagination": pagination})

    def create(self, body: Json) -> httpx.Response:
        """Create an object (201)."""
        return httpx.Response(httpx.codes.CREATED, json=self.insert(body))

    def update(self, object_id: str, body: Json) -> httpx.Response:
        """Merge the fields into the object (200)."""
        found = self.find(object_id)
        if found is None:
            return problem(HTTP_NOT_FOUND, NOT_FOUND)
        found.update(body)
        return httpx.Response(httpx.codes.OK, json=found)

    def delete(self, object_id: str) -> httpx.Response:
        """Remove the object (204)."""
        if self.records.pop(object_id, None) is None:
            return problem(HTTP_NOT_FOUND, NOT_FOUND)
        return httpx.Response(httpx.codes.NO_CONTENT)


class Pods(Collection):
    """Pods: created from a template, terminated by DELETE."""

    def __init__(self, api: "FakeRunpodApi") -> None:
        """Start empty."""
        super().__init__(api, "pods", "pod", newest_first=True)
        # Bodies of every `POST /pods` that reached the fake, in order.
        self.create_bodies: list[Json] = []

    def add(self, name: str, status: str = FAKE_POD_START_STATUS, env: dict[str, str] | None = None) -> Json:
        """Seed a pod (its `env` is served like Runpod's: secrets included)."""
        body = {NAME: name, STATUS: status, ENV: dict(env or {}), "template": None, "cost": 0.27}
        return self.insert({**body, "startedAt": None, "mounts": {}})

    def create(self, body: Json) -> httpx.Response:
        """Create a pod if there is capacity, otherwise queue it (with `queue`) or answer 400."""
        self.create_bodies.append(body)
        settings = self.api.settings
        if settings.capacity:
            return httpx.Response(httpx.codes.CREATED, json=self.start(body))
        if "queue" not in body:
            return problem(HTTP_CLIENT_ERROR, "There are no instances currently available")
        queued = self.api.queued_pods.queue_up(body)
        location = f"{FAKE_API_PREFIX}queued-pods/{queued['id']}"
        return httpx.Response(HTTP_ACCEPTED, json=queued, headers={"Location": location})

    def start(self, body: Json) -> Json:
        """Store the pod a create body (or a deploying queued pod) describes."""
        template = self.api.templates.find(str(body.get("templateId")))
        env = {**(template or {}).get(ENV, {}), **body.get(ENV, {})}
        pod = {NAME: body[NAME], STATUS: FAKE_POD_START_STATUS, ENV: env, "cost": self.api.settings.gpu_price}
        return self.insert({**pod, "template": body.get("templateId"), "mounts": body.get("mounts", {})})

    def delete(self, object_id: str) -> httpx.Response:
        """Terminate the pod; it stays listed as TERMINATED unless `keep_terminal` is off."""
        found = self.find(object_id)
        if found is None:
            return problem(HTTP_NOT_FOUND, NOT_FOUND)
        if not self.api.settings.terminate_effective:
            return httpx.Response(httpx.codes.NO_CONTENT)
        found[STATUS] = POD_TERMINATED
        if not self.api.settings.keep_terminal:
            self.records.pop(object_id)
        return httpx.Response(httpx.codes.NO_CONTENT)

    def live(self) -> list[Json]:
        """Return every pod not yet terminated (listed or not)."""
        return [record.body for record in self.records.values() if record.body[STATUS] != POD_TERMINATED]


class QueuedPods(Collection):
    """Queued pods: deploy on the schedule of the settings, expire, or get cancelled."""

    def add(self, name: str, status: str = QUEUED_POD_QUEUED, expires_at: datetime | None = None) -> Json:
        """Seed a queued pod."""
        expires = expires_at or self.api.now() + timedelta(hours=1)
        return self.insert(_queued_body({NAME: name, "queue": {"expiresAt": iso(expires)}}, status))

    def queue_up(self, body: Json) -> Json:
        """Store a queued pod for a create body that found no capacity."""
        queued = self.insert(_queued_body(body, QUEUED_POD_QUEUED))
        record = self.records[queued["id"]]
        record.request = body
        deploy_after = self.api.settings.deploy_after
        if deploy_after is not None:
            record.deploy_at = self.api.now() + deploy_after
        return queued

    def schedule_deploy(self, queued_pod_id: str, moment: datetime) -> None:
        """Let a queued pod (created through `POST /pods`) deploy at the first request from this moment on."""
        self.records[queued_pod_id].deploy_at = moment

    def advance(self) -> None:
        """Deploy queued pods whose time came; expire those past `expiresAt`."""
        now = self.api.now()
        for record in self.records.values():
            if record.body[STATUS] != QUEUED_POD_QUEUED:
                continue
            if record.deploy_at is not None and record.deploy_at <= now and record.request is not None:
                pod = self.api.pods.start(record.request)
                record.body.update({STATUS: QUEUED_POD_DEPLOYED, "podId": pod["id"], "resolvedAt": iso(now)})
            elif datetime.fromisoformat(record.body["queue"]["expiresAt"]) <= now:
                record.body.update({STATUS: QUEUED_POD_EXPIRED, "resolvedAt": iso(now)})

    def delete(self, object_id: str) -> httpx.Response:
        """Cancel a waiting queued pod (204 whatever its status; a deployed pod stays)."""
        found = self.find(object_id)
        if found is None:
            return problem(HTTP_NOT_FOUND, NOT_FOUND)
        if found[STATUS] == QUEUED_POD_QUEUED:
            found.update({STATUS: QUEUED_POD_CANCELLED, "resolvedAt": iso(self.api.now())})
        return httpx.Response(httpx.codes.NO_CONTENT)


def _queued_body(body: Json, status: str) -> Json:
    queue = body.get("queue") or {}
    expires = queue.get("expiresAt")
    return {
        NAME: body[NAME],
        STATUS: status,
        "podId": None,
        "failureReason": None,
        "resolvedAt": None,
        "queue": {"startsAt": None, "expiresAt": expires},
        "templateId": body.get("templateId"),
    }


class Templates(Collection):
    """Templates (no `createdAt` on Runpod; the fake's extra field is ignored by the client)."""

    def add(self, name: str, image: str, env: dict[str, str], *, start_ssh: bool = True) -> Json:
        """Seed a template."""
        return self.insert({NAME: name, "image": image, ENV: dict(env), "startSsh": start_ssh, "startJupyter": True})

    def create(self, body: Json) -> httpx.Response:
        """Create a template with Runpod's defaults (SSH and Jupyter on); 422 for unknown fields."""
        if not set(body) <= TEMPLATE_FIELDS or not {NAME, "image"} <= set(body):
            return problem(VALIDATION_FAILED, "invalid template")
        return super().create({"startSsh": True, "startJupyter": True, ENV: {}, **body})

    def update(self, object_id: str, body: Json) -> httpx.Response:
        """Change the given fields; 422 for unknown fields."""
        if not set(body) <= TEMPLATE_FIELDS:
            return problem(VALIDATION_FAILED, "invalid template")
        return super().update(object_id, body)

    def delete(self, object_id: str) -> httpx.Response:
        """Delete a template; 400 while a live pod was created from it."""
        if any(pod["template"] == object_id for pod in self.api.pods.live()):
            return problem(HTTP_CLIENT_ERROR, "template in use")
        return super().delete(object_id)


class Secrets(Collection):
    """Secrets: unique names (case-insensitive), values kept apart and never served."""

    def __init__(self, api: "FakeRunpodApi") -> None:
        """Start empty."""
        super().__init__(api, "secrets", "sec")
        # Secret id -> value, for assertions.
        self.secret_values: dict[str, str] = {}

    def add(self, name: str, secret_value: str) -> Json:
        """Seed a secret."""
        stored = self.insert({NAME: name, "description": None})
        self.secret_values[stored["id"]] = secret_value
        return stored

    def create(self, body: Json) -> httpx.Response:
        """Create a secret; 422 for an invalid name or unknown fields, 409 if the name is taken."""
        name = str(body.get(NAME, ""))
        valid = re.fullmatch(VAULT_NAME_PATTERN, name) is not None
        if not valid or name.casefold().startswith(RESERVED_VAULT_PREFIX) or not set(body) <= SECRET_FIELDS:
            return problem(VALIDATION_FAILED, "invalid secret")
        if any(record.body[NAME].casefold() == name.casefold() for record in self.records.values()):
            return problem(HTTP_CONFLICT, "a secret with this name already exists")
        return httpx.Response(httpx.codes.CREATED, json=self.add(name, str(body["value"])))

    def update(self, object_id: str, body: Json) -> httpx.Response:
        """Replace the value."""
        if self.find(object_id) is None:
            return problem(HTTP_NOT_FOUND, NOT_FOUND)
        self.secret_values[object_id] = str(body["value"])
        return super().update(object_id, {"valueLastUpdatedAt": iso(self.api.now())})

    def by_name(self, name: str) -> list[str]:
        """Return the values of the secrets with this name."""
        return [
            self.secret_values[object_id] for object_id, record in self.records.items() if record.body[NAME] == name
        ]


class Volumes(Collection):
    """Global Volumes: names need not be unique; 409 on delete while a live pod mounts one."""

    def add(self, name: str) -> Json:
        """Seed a Global Volume."""
        return self.insert({NAME: name, "createdByUserId": None})

    def create(self, body: Json) -> httpx.Response:
        """Create a Global Volume."""
        return httpx.Response(httpx.codes.CREATED, json=self.add(str(body[NAME])))

    def delete(self, object_id: str) -> httpx.Response:
        """Delete a Global Volume unless mounted."""
        for pod in self.api.pods.live():
            if any(mount.get("volumeId") == object_id for mount in pod["mounts"].get("global", [])):
                return problem(HTTP_CONFLICT, "volume in use")
        return super().delete(object_id)


class FakeRunpodApi:
    """The fake API; give `transport()` to the client."""

    def __init__(self, now: Now, *, api_key: str = FAKE_API_KEY, settings: FakeSettings | None = None) -> None:
        """Serve an empty account, timed by `now`, to requests carrying `api_key`."""
        self._now = now
        self.settings = settings or FakeSettings()
        self._seen: list[SeenRequest] = []
        self.pods = Pods(self)
        self.queued_pods = QueuedPods(self, "queuedPods", "qpod", newest_first=True)
        self.templates = Templates(self, "templates", "tpl")
        self.secrets = Secrets(self)
        self.volumes = Volumes(self, "globalVolumes", "gsb_")
        self._api_key = api_key
        self._failures: dict[tuple[str, str], deque[Failure]] = {}

    def now(self) -> datetime:
        """Return the fake's current time."""
        return self._now()

    def seen(self) -> list[SeenRequest]:
        """Return every request received so far."""
        return list(self._seen)

    def transport(self) -> httpx.MockTransport:
        """Return a transport that hands every request to the fake."""
        return httpx.MockTransport(self.respond)

    def fail(self, method: str, path: str, *failures: Failure) -> None:
        """Apply these failures, one each, to the next requests with this method and path (`/v2/...`)."""
        self._failures.setdefault((method, path), deque()).extend(failures)

    def count(self, method: str, path: str) -> int:
        """Return how many requests with this method and path (`/v2/...`) arrived."""
        return sum(1 for seen in self._seen if (seen.method, seen.path) == (method, path))

    def respond(self, request: httpx.Request) -> httpx.Response:
        """Answer one request (raising the transport errors a failure asks for)."""
        authorised = request.headers.get("Authorization") == f"Bearer {self._api_key}"
        path = request.url.path
        timeout = request.extensions.get("timeout", {})
        timeouts = (timeout.get("connect"), timeout.get("read"))
        self._seen.append(SeenRequest(request.method, path, request.url.query.decode(), authorised, timeouts))
        hook = self.settings.on_request
        if hook is not None:
            hook(request)
        if request.url.host != FAKE_API_HOST or not path.startswith(FAKE_API_PREFIX):
            return problem(HTTP_NOT_FOUND, "unknown host or path")
        if not authorised:
            return problem(HTTP_UNAUTHORIZED, "invalid API key")
        queued = self._failures.get((request.method, path))
        failure = queued.popleft() if queued else Failure()
        refused = _refusal(failure, request)
        if refused is not None:
            return refused
        response = self._serve(request, path.removeprefix(FAKE_API_PREFIX))
        return _after(failure, request, response)

    def _serve(self, request: httpx.Request, path: str) -> httpx.Response:
        self.queued_pods.advance()
        if path == "catalog/gpus":
            gpu = {
                "id": FAKE_GPU_ID,
                NAME: "RTX A5000",
                "price": {"secure": self.settings.gpu_price, "community": 0.16},
            }
            return httpx.Response(httpx.codes.OK, json={"gpus": [gpu]})
        collections: dict[str, Collection] = {
            "pods": self.pods,
            "queued-pods": self.queued_pods,
            "templates": self.templates,
            "account/secrets": self.secrets,
            "global-volumes": self.volumes,
        }
        collection, object_id = _locate(collections, path)
        if collection is None:
            return problem(HTTP_NOT_FOUND, "unknown path")
        body = json.loads(request.content) if request.content else {}
        if object_id is None:
            return self._on_collection(collection, request, body)
        return self._on_object(collection, object_id, request.method, body)

    def _on_collection(self, collection: Collection, request: httpx.Request, body: Json) -> httpx.Response:
        if request.method == "POST":
            return collection.create(body)
        if request.method != "GET":
            return problem(httpx.codes.METHOD_NOT_ALLOWED, "method not allowed")
        return collection.page(request.url.params)

    def _on_object(self, collection: Collection, object_id: str, method: str, body: Json) -> httpx.Response:
        if method == "DELETE":
            return collection.delete(object_id)
        if method == "PATCH":
            return collection.update(object_id, body)
        found = collection.find(object_id)
        if method != "GET" or found is None:
            return problem(HTTP_NOT_FOUND, NOT_FOUND)
        return httpx.Response(httpx.codes.OK, json=found)


def _locate(collections: dict[str, Collection], path: str) -> tuple[Collection | None, str | None]:
    for prefix, collection in collections.items():
        rest = path.removeprefix(f"{prefix}/")
        if path == prefix:
            return collection, None
        if rest != path and "/" not in rest:
            return collection, rest
    return None, None


def _after(failure: Failure, request: httpx.Request, response: httpx.Response) -> httpx.Response:
    """Return the answer of a request carried out: as served, or changed or lost as the failure asks."""
    if failure.garbled_body:
        return httpx.Response(response.status_code, json={"unexpected": "shape"})
    if failure.status_in_body is not None:
        served = {**json.loads(response.content), STATUS: failure.status_in_body}
        return httpx.Response(response.status_code, json=served)
    if failure.lost_response:
        # Like real transport errors, the message holds request data (here: the key).
        msg = f"fake: response lost ({request.headers.get('Authorization')})"
        raise httpx.ReadTimeout(msg, request=request)
    return response


def _refusal(failure: Failure, request: httpx.Request) -> httpx.Response | None:
    if failure.connect_error:
        msg = f"fake: connection refused ({request.headers.get('Authorization')})"
        raise httpx.ConnectError(msg, request=request)
    if failure.redirect_to is not None:
        return httpx.Response(httpx.codes.FOUND, headers={"Location": failure.redirect_to})
    if failure.status is None:
        return None
    response = problem(failure.status, "injected failure")
    if failure.retry_after is not None:
        response.headers["Retry-After"] = failure.retry_after
    return response
