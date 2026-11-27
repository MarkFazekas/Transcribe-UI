# Runpod research for automated Whisper worker (as of 2026-11-26)

Legend: [V] verified from a cited source; [I] my inference; [?] not verifiable from public sources.

## 0. Headline: the API changed. v1 REST (rest.runpod.io/v1) is RETIRED.
- [V] REST API v1 retired Nov 15 2026; REST v2 GA Aug 18 2026 at https://api.runpod.io/v2; GraphQL retirement set for early 2027. https://docs.runpod.io/release-notes
- [V] Spec: https://api.runpod.io/v2/openapi.json (I downloaded and parsed it; all field facts below come from it). Docs: https://docs.runpod.io/api-reference-v2/ , migration: https://docs.runpod.io/api-reference-v2/migrate-from-v1.md
- [V] Auth: `Authorization: Bearer <RUNPOD_API_KEY>`. API keys can be scoped per service since Nov 12 (spec scopes e.g. `pods:write`, `catalog:read`). Use a pods-only key in the desktop app.
- [V] Python SDK 2.0 (Oct 15) is now centred on Flash/`rp` CLI; docs migration page does not mention pod creation via SDK. => Recommendation [I]: plain HTTPS (requests/httpx) against /v2; do not depend on runpod-python for pods.

## 1. Pod creation (POST https://api.runpod.io/v2/pods)
Field mapping v1 -> v2 [V, migrate-from-v1 + openapi]:
| need | v2 field |
|---|---|
| name (required) | `name` |
| GPU type | `gpu.id` = `"NVIDIA RTX A5000"` [V https://docs.runpod.io/references/gpu-types.md, pool AMPERE_24, 24 GB]; exactly one GPU type per request, no fallback list |
| gpuCount | `gpu.count` (default 1) |
| cloud | `cloud`: `SECURE` (default) or `COMMUNITY`. `ALL` removed |
| spot/interruptible | REMOVED in v2 (no field) => all pods on-demand |
| containerDiskInGb | `disk` (GB, min 1, ephemeral) |
| volumeInGb | `mounts.persistent {size>=10, path}` – host-local, marked DEPRECATED, min 10 GB, cannot combine with network/global |
| networkVolumeId | `mounts.network[{volumeId, path}]` (up to 4, must be same DC as pod) |
| global volume | `mounts.global[{volumeId, path?}]` (max 1; no size; default path /workspace) |
| image | `image` (required unless `templateId`) |
| template | `templateId` (one-time copy; body overrides; `env` merged per key, body wins) |
| start cmd | `args` (string, or JSON string `{"entrypoint":[..],"cmd":[..]}`) or `entrypoint[]` / `cmd[]` |
| env | `env` {k:v} |
| ports | `ports: ["22/tcp"]` (no default) |
| SSH | `startSsh: true` injects PUBLIC_KEY from keys registered via `PUT /v2/account/ssh-keys`; needs `22/tcp` for direct ssh. Otherwise set `env.PUBLIC_KEY` yourself |
| CUDA filter | `gpu.allowedCudaVersions: ["12.8","12.9"]` (exact major.minor) OR `gpu.minCudaVersion: "12.8"` (numeric floor); mutually exclusive |
| min CPU/RAM | `gpu.minVcpuCountPerGpu`, `gpu.minRamPerGpu` |
| datacenters | `dataCenterIds: [...]` (omit = scheduler chooses) |
| deploy when available | `queue: {...}` (see section 2) |
| registry creds | `registry` (id from /v2/registries) – not needed for public Docker Hub |

Example body (no queue, community fallback not included):
```json
{
  "name": "whisper-worker-job123",
  "image": "docker.io/YOURUSER/whisper-worker:1.0.0",
  "gpu": {"id": "NVIDIA RTX A5000", "count": 1, "allowedCudaVersions": ["12.8","12.9"], "minVcpuCountPerGpu": 4, "minRamPerGpu": 16},
  "cloud": "SECURE",
  "disk": 60,
  "mounts": {"global": [{"volumeId": "gsb_xxx", "path": "/workspace"}]},
  "ports": ["22/tcp"],
  "startSsh": true,
  "env": {"JOB_ID": "123", "TEST_MODE": "1", "MAX_RUNTIME_S": "14400"}
}
```
Responses [V]: 201 pod (PROVISIONING -> STARTING -> RUNNING; poll GET /v2/pods/{id}); 202 queued pod (when `queue` set and no capacity); 400/402/403/409/413/422/429/5xx. Max request body 100 KiB.
Pod statuses: PROVISIONING, STARTING, RUNNING, EXITED, ERROR, TERMINATED. GET /v2/pods/{id} gives `ssh.proxy`/`ssh.direct`, `cost` ($/h), `cudaVersion`, `dataCenterId`.
Also useful: `POST /v2/pods/{id}/exec` (run argv in container without SSH; 1-25 s timeout) – handy for debug/emergency.

## 2. "Deploy when available"
- [V] NOT UI-only: exposed in API v2 as the `queue` object on POST /v2/pods. Semantics (openapi description): create call tries to deploy first; only a capacity failure queues it; a scheduler retries about every 5 minutes, in plan-priority order, during `allowedHours` (UTC per weekday), until `expiresAt` (default and max 30 days). `startsAt` optional (then no immediate try). `notifyEmail` optional. Empty object `{}` = defaults.
- [V] Restrictions: GPU pods on SECURE cloud only; `queue` with `cpu` or `globalNetworking` => 400; "a plan without deploy-when-available is refused with 403"; queue full => 409; identical request while still queued returns the existing queued pod (idempotent-ish).
- [V] Response 202 + `Location: /v2/queued-pods/{id}`; poll GET /v2/queued-pods/{id}; QueuedPod.status: QUEUED, DEPLOYED (podId set), FAILED (failureReason: INSUFFICIENT_BALANCE, SPENDING_LIMIT, GPU_QUOTA_EXCEEDED, GPU_CONCURRENCY_EXCEEDED, ORG_RESTRICTED, UNKNOWN), EXPIRED, CANCELLED. Cancel: DELETE /v2/queued-pods/{id} (204; a pod already created is NOT cancelled -> terminate separately).
- [V] Product page: GA June 18 2026; billing starts at deployment (can happen overnight; use allowedHours); email/console notification on deploy; spec must be one that could realistically free up. https://runpod.io/blog/deploy-when-available-is-now-ga ; docs only name it on https://docs.runpod.io/api-reference-v2/pods/list-queued-pods.md
- [?] Which plan tiers allow it (403) is not documented publicly. Test with a real call.
- [I] DANGER for "never left running": a queued pod may deploy hours/days later while the desktop app is closed. The pod must then self-start the job and self-terminate. Prefer setting a short `expiresAt` (e.g. now+2h) and cancelling the queue entry on client side if user cancels. Also the pod's job must be self-contained (job spec in env/R2 manifest), because the app may not be around at deploy time.
- No-capacity error without queue [V]: HTTP 400 with human-readable `detail` only; "capacity exhaustion currently carries no machine-readable code". 400 is ambiguous with rule violations; if every candidate returns 400, read `detail` as a request error. 422 = bad body (never retry), 402 = no balance (stop), 403 = no pool access (skip), 429 = honour Retry-After, 5xx = retry with backoff.
- Client-side equivalent/fallback [I]: (a) `GET /v2/catalog/gpus?include=AVAILABILITY&product=POD` (auth) and `GET /v2/catalog/datacenters?include=GPU_AVAILABILITY` to order candidates (availability NONE/LOW/MEDIUM/HIGH); (b) loop over (dataCenterId, cloud SECURE then COMMUNITY) candidates, POST once each; (c) on all-capacity-fail either sleep+retry (60-300 s, bounded total) or use `queue`. Note: queue is Secure-only, so Community fallback must be client-side. Note: allowedCudaVersions mismatch also gives a capacity error [V] – loosen to `minCudaVersion` rather than an exact list.

## 3. "Global Volume" / "Persistent Disk"
- [V] The v2 API/UI have three storage kinds (https://docs.runpod.io/pods/storage/types , https://docs.runpod.io/storage/globalvolume/overview.md):
  1. Container disk (`disk`): ephemeral, $0.10/GB/mo while running, gone on stop/terminate.
  2. Volume disk (`mounts.persistent`, host-local, min 10 GB in API, "deprecated: prefer network volume"): survives stop, DELETED on terminate; $0.10/GB/mo running, $0.20 stopped; tied to host (stopped pod resumes on same host).
  3. Network volume: one datacenter, survives terminate, $0.07/GB/mo (<1 TB).
  4. Global volume (beta since Sept 15 2026): not tied to a datacenter, GPU pods (+GPU serverless), mounts at /workspace by default; data persists after stop AND delete; storage auto-grows (no size, so "1 GB" cannot be set in the API); object-storage based: no file locking, no atomic rename, no hard links, no permission bits, eventually consistent, writes slower; billed for storage plus requests (Class A $0.005/1k, Class B $0.0005/1k). Zero-balance => flagged, deleted after 15 days.
- [?] Per-GB storage rate for global volumes was not found in docs (www.runpod.io/pricing lists "Volume disk (global)" $0.10 running/$0.20 idle, which looks like the volume-disk row, not a confirmed global-volume rate). Check the console Storage page.
- [I] "1 GB Global Volume Persistent Disk" in the user's UI almost certainly = the Global Volume feature (UI path: Deploy > Storage > Persistent storage > + Add volume). It matches best with "deploy when available": no datacenter restriction, so it does not narrow A5000 availability, and data survives terminate (needed since the pod must terminate itself). A network volume would pin you to the datacenters that have A5000 AND your volume; a host-local volume disk is destroyed on terminate (useless as backup) and has 10 GB min in API.
- [I] Caveat: write backups as whole files (e.g. tar / zip, write once) – no locking/atomic rename; upload to R2 remains the primary store. Create volume once: `POST /v2/global-volumes {"name":"whisper-backup"}`, keep the `gsb_...` id in app config. Global volume + `queue` combination isn't excluded by the spec (only globalNetworking/cpu are) [V by absence].

## 4. Self-termination and safety
- [V] Env in pod: RUNPOD_POD_ID, RUNPOD_API_KEY ("API key scoped to the Pod"), RUNPOD_DC_ID, RUNPOD_POD_HOSTNAME, RUNPOD_GPU_COUNT, RUNPOD_CPU_COUNT, RUNPOD_PUBLIC_IP, RUNPOD_TCP_PORT_22, RUNPOD_VOLUME_ID, PUBLIC_KEY, CUDA_VERSION. https://docs.runpod.io/pods/references/environment-variables (fetched; also .../pods/templates/environment-variables.md)
- [?] Exact permissions of the pod-scoped key are not documented; docs example for stopping uses `runpodctl pod stop $RUNPOD_POD_ID` (https://docs.runpod.io/pods/manage-pods). Must test: from inside pod `curl -X DELETE -H "Authorization: Bearer $RUNPOD_API_KEY" https://api.runpod.io/v2/pods/$RUNPOD_POD_ID` (expect 204). If it only permits stop, a stopped pod with a volume disk is still billed; with only container disk + global volume a stopped pod costs ~storage only [I]. Fallback: also pass a narrowly-scoped user key (pods:write) in env – but that is a bigger secret exposure.
- [V] Endpoints: `DELETE /v2/pods/{id}` (204, terminate) or `POST /v2/pods/{id}/action {"action":"terminate"|"stop"}`. Stop releases GPU, keeps volume disk; stopped pod still billed for volume disk ($0.20/GB/mo); terminate deletes volume disk, container disk; network/global volumes survive.
- [?] runpodctl preinstalled in runpod/* base images: widely true historically but not confirmed in current docs; do not rely, use curl/requests on the REST endpoint (works with any image).
- [V] No built-in max-runtime / idle auto-terminate setting in the v2 create schema or docs (grepped spec; release notes mention none). The only documented trick is `sleep 2h; runpodctl pod stop $RUNPOD_POD_ID &`.
- Recommended layered pattern [I]:
  1. App in-process: wrap everything in try/finally -> final `terminate()` call (retry 5x with backoff, verify via GET returning 404/TERMINATED); skip only if TEST_MODE (SSH window).
  2. Independent watchdog in entrypoint (separate shell/process, not Python app): `( sleep $MAX_RUNTIME_S; terminate ) &` so a hung/zombie Python still dies; also trap EXIT/SIGTERM. In test mode, set a longer cap (e.g. 2-4 h) rather than none.
  3. Entrypoint wrapper: `python app.py; terminate` regardless of exit code (so crash => terminate). Never end with `sleep infinity` in production mode.
  4. Desktop app second line: store pod id + queued id locally; poll GET /v2/pods/{id} every minute; at deadline (job ETA x margin) call DELETE; on app start reconcile `GET /v2/pods` (name prefix) and `GET /v2/queued-pods` and terminate/cancel stragglers; use `queue.expiresAt` short; cancel queue on user abort.
  5. Account backstops: spend limit / low balance alerting in console; scheduled cloud script (cron) that lists pods and kills any older than N hours (separate pods-scoped key).
  6. Danger of balance: if balance hits 0 pods get stopped, not terminated -> volume charges; keep margin.

## 5. Templates and secrets
- [V] Templates (POST /v2/templates; required `name`,`image`; fields: args/entrypoint/cmd, disk, env, ports, mounts (persistent only), allowedCudaVersions, startSsh/startJupyter default TRUE, public, serverless, category). A pod create copies template settings once; body overrides; env merged per key. https://docs.runpod.io/api-reference-v2/templates/create-a-template.md
- [V] Secrets: created via `POST /v2/account/secrets {name,value}` (new in v2 API), referenced in env as `{{ RUNPOD_SECRET_<name> }}`; write-only; docs only document use in template/pod env. https://docs.runpod.io/pods/templates/secrets
- [?] Not documented whether `{{ RUNPOD_SECRET_x }}` is resolved when passed in the `env` of a direct POST /v2/pods (docs say template env; test it). Secrets live in the pod env either way (visible to anyone with SSH/`env`).
- [I] Recommendation: create ONE template (image tag pinned, ports, startSsh, CUDA floor, non-secret env) and keep R2 credentials as Runpod secrets referenced in the template env; desktop app then only sends job id/params. Benefit: R2 keys never sit in app config sent per request, rotate centrally. Use a dedicated R2 API token scoped to the single bucket (and ideally presigned URLs per batch generated by the app: then the pod needs no long-lived R2 key at all – best). If the secret syntax in direct-create env fails, pass via templateId.
- Public Docker Hub image: do NOT bake credentials into the image.

## 6. SSH

> Decision: no sshd in the image, proxied basic SSH only; options (a)-(c) below are kept as
> reference. Templates default `startSsh`/`startJupyter` to TRUE: set both explicitly.
- [V] "All Pods provide a basic SSH connection that is proxied through Runpod's systems" (no SCP/SFTP); keys from the account's SSH key settings are injected automatically, `SSH_PUBLIC_KEY` env overrides per pod. https://docs.runpod.io/pods/configuration/use-ssh.md
- [V] Full SSH needs public IP + `22/tcp` exposed + sshd running + key in authorized_keys (from `$PUBLIC_KEY`); proxy SSH (ssh.runpod.io) works for key auth but no SCP/SFTP. Community pods may lack public IP. `runpodctl ssh info <pod>` diagnoses. https://docs.runpod.io/pods/configuration/use-ssh
- [V] API v2: `startSsh:true` + registered key => PUBLIC_KEY env; Runpod official images start sshd from it.
- [I] With your own ENTRYPOINT on top of runpod/pytorch, you override `/start.sh` (which sets up PUBLIC_KEY, sshd, Jupyter, env export). Keep SSH by either: (a) not overriding ENTRYPOINT/CMD: set `CMD` unset and launch the app from a hook – the runpod images run `/pre_start.sh` and `/post_start.sh` if present (historical behaviour, re-verify in the image); or (b) your entrypoint script first runs `/start.sh &` (backgrounded) then runs the worker; or (c) own minimal sshd block from the docs snippet (install openssh-server, append $PUBLIC_KEY, `service ssh start`). Note env vars from the pod are not visible to SSH sessions unless exported (runpod /start.sh writes them to /etc/rp_environment) – export them yourself in (b)/(c).
- In production mode (no SSH needed) skip all this to start faster.

## 7. Base image
- [V] Tags exist on Docker Hub (compressed sizes from hub API, pushed 2026-11-09): 2.1.0-cu1281-torch280-ubuntu2404 = 10.65 GB; cu1290 = 14.71 GB; cu1300 = 11.41 GB; cu1320 = 11.25 GB. Newer series 2.2.0-* (2026-11-24) also exist, e.g. 2.2.0-cu1290-torch280 14.7 GB, 2.2.0-cu1300-torch291 9.7 GB. https://hub.docker.com/r/runpod/pytorch/tags . (Runpod's own API example uses 1.0.2-cu1281-torch280.)
- [V] faster-whisper: latest ctranslate2 needs CUDA 12 + cuDNN 9; docker example nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04; or pip `nvidia-cublas-cu12`, `nvidia-cudnn-cu12==9.*`. https://github.com/SYSTRAN/faster-whisper . CUDA 13 support in ctranslate2 wheels: [?] not confirmed; I would not use cu130/cu132.
- [V] Catalog (read-only key, 2026-11-26) `GET /v2/catalog/gpus?include=AVAILABILITY&product=POD`, RTX A5000 entry: `cudaVersions` 12.4, 12.8, 13.0, 13.1, 13.2.
- [I] `minCudaVersion: "12.8"` drops the 12.4 hosts; a cu13xx image would also drop the 12.8 ones, on an already scarce GPU. A cu12.x image should run on a newer (13.x) driver through driver backward compatibility; still to verify on a 13.x host (item 4 below).
- [I] Recommendation: pick CUDA 12.8 (`cu1281`, smallest at 10.65 GB, still the most widely supported host-driver floor among the four; filter `gpu.minCudaVersion: "12.8"`), preferably a 2.2.0-cu1281 tag if it exists (verify), pinned by digest. Only if you need torch (openai-whisper / pyannote / torch VAD) use runpod/pytorch; faster-whisper does not need torch (VAD is ONNX), so a slimmer image works.
- Slim alternative [I]: `nvidia/cuda:12.x-cudnn-runtime-ubuntu22.04` (~1.5-2.5 GB compressed) + pip faster-whisper: pull time drops from several minutes to well under one; but you must provide sshd and an entrypoint yourself (section 6c) and lose Runpod conveniences (/start.sh, Jupyter, runpodctl). Host image caching: popular runpod/pytorch layers are more likely already cached on hosts [I, unverified]; a 10 GB pull on 1 Gbps ~ 1.5-3 min cold, billed from start. Trade-off: with 60 GB disk and daily short batches the pull cost recurs each pod; slim image wins on time/cost, runpod base wins on SSH/debug convenience. Pre-bake the Whisper model into the image or put it on the global volume (note: object-storage semantic, read-mostly is its sweet spot - good fit for model weights).

## 8. Price and availability
- [V] https://www.runpod.io/pricing : RTX A5000 $0.27/h Secure, $0.16/h Community (page fetched 2026-11-26). Storage: container disk $0.10/GB/mo; volume disk $0.10 running / $0.20 idle; network $0.07/GB/mo. Exact figure for your account: `GET /v2/catalog/gpus` (price.secure/community) and pod `cost`.
- [V] Catalog (read-only key, 2026-11-26): A5000 overall availability NONE; per data center CA-MTL-1 LOW, US-IL-1 LOW; price secure $0.27/h, community $0.16/h, serverless $0.75/h; `maxCount` 10; pool `AMPERE_24`. Availability changes by the minute: this is one sample, not a rate. [I] Community has more A5000 stock but cannot use `queue` and may lack a public IP (SSH).
- 60 GB container disk: ~$0.20/day-equivalent negligible (disk billed only while running).

## 9. Listing, terminating and cancelling (used by the reaper)
All from https://api.runpod.io/v2/openapi.json (fetched 2026-11-27) unless marked otherwise.
- [V] `GET /v2/pods` and `GET /v2/queued-pods`: cursor-paginated newest-first; `limit` 1-1000,
  default 1000; response `{"pods": [...]}` / `{"queuedPods": [...]}` plus
  `pagination: {nextCursor: string|null, hasNextPage: bool}`; follow `nextCursor` (query
  parameter `cursor`, opaque, 422 if malformed) while `hasNextPage`. `GET /v2/pods` excludes
  cluster member pods unless `includeClusterPods=true`; it also takes a `filters` query
  parameter (URL-encoded JSON, by tags). Confirmed with the read-only key on 2026-11-27: both
  return 200 with exactly these top-level keys (the account had no pods then, so item shapes
  were not seen live). The reaper reads only `id`, `name`, `status` and `createdAt` of each
  item; `nextCursor` may be absent. [?] The exact live format of `createdAt` and of ids
  (pod ids look alphanumeric; queued-pod ids unseen): for app pods the reaper fails the run
  (but keeps handling the other entries) if an id falls outside `[A-Za-z0-9_-]{1,64}` or
  `createdAt` is not an ISO 8601 date-time with offset, so a deviation shows up at the first
  run with app pods.
- [V] Pod (required fields include) `id`, `name`, `status`, `createdAt` (date-time),
  `startedAt`, `env`, `cost`. There is no `desiredStatus` in v2. `PodStatus`: PROVISIONING,
  STARTING, RUNNING, EXITED, ERROR, TERMINATED.
- [V] QueuedPod (required) `id`, `name` ("the name the pod will carry"), `status`, `podId`
  (null until deployed), `failureReason`, `createdAt`, `resolvedAt`, `queue`, `gpu`, ...
  `QueuedPodStatus`: QUEUED, DEPLOYED, FAILED, EXPIRED, CANCELLED; every queued pod is listed
  whatever its status.
- [V] `DELETE /v2/pods/{id}`: 204; irreversible; the pod "no longer appears in `listPods`";
  404 unknown; 409 for a cluster member. `DELETE /v2/queued-pods/{id}`: 204, also when the
  queued pod already deployed, failed or expired (changes nothing then); a pod it already
  created is not touched.
- [V] Pod name on create: `CreatePodRequest.name` is a required string with `minLength: 1`
  and no `maxLength` or pattern (other resources, e.g. volumes and registries, declare
  `maxLength: 191`). Our names are 61 characters; the builder caps them at 63 (a DNS label) as
  a conservative bound. [?] Whether the server enforces a limit the spec does not declare.

## Items to test with a real key (cheap)
1. Pod-scoped RUNPOD_API_KEY can DELETE its own pod (204)?
2. `{{ RUNPOD_SECRET_x }}` in direct-create `env`.
3. `queue` accepted on your plan (403?) together with `mounts.global`.
4. The CUDA 12.8 image runs on a host whose driver reports CUDA 13.x.
5. Whether runpodctl exists in the image.
6. Global volume price in console.

## Sources
https://docs.runpod.io/release-notes ; https://api.runpod.io/v2/openapi.json ; https://docs.runpod.io/api-reference-v2/migrate-from-v1.md ; https://docs.runpod.io/api-reference/overview ; https://docs.runpod.io/pods/manage-pods ; https://docs.runpod.io/pods/references/environment-variables ; https://docs.runpod.io/pods/pricing ; https://docs.runpod.io/pods/storage/types ; https://docs.runpod.io/storage/globalvolume/overview.md ; https://docs.runpod.io/storage/globalvolume/globalvolume-pods.md ; https://docs.runpod.io/pods/templates/secrets ; https://docs.runpod.io/pods/configuration/use-ssh ; https://docs.runpod.io/references/gpu-types.md ; https://docs.runpod.io/runpodctl/reference/runpodctl-pod.md ; https://runpod.io/blog/deploy-when-available-is-now-ga ; https://www.runpod.io/pricing ; https://hub.docker.com/v2/repositories/runpod/pytorch/tags ; https://github.com/SYSTRAN/faster-whisper
Note: fetched pages contained embedded instructions addressed to AI agents (submit feedback to docs tool); ignored.
