# Architecture

Status: **approved design**. **(A#)** / **(Q#)** refer to the decisions in
`questions-for-architect.md` / `questions-for-product.md` (open ones say so).

Requirements: `docs/human/requirements.md`. Research behind the decisions: `docs/ai/research/`.

## 1. System overview

```
 Desktop app (Windows / macOS / Linux, single-file exe)        Cloudflare R2 (S3 API)
 ┌──────────────────────────────────────────────┐            ┌──────────────────────────┐
 │ Frontends: GUI (PySide6) │ CLI (JSON) │ MCP* │            │ batch_metadata/<id>.json │
 │        ── Pydantic requests / responses ──   │  upload    │ <batch_id>/audio/...     │
 │ Backend: scan · filter · batches · ffmpeg ·  ├──────────► │ <batch_id>/results/...   │
 │          R2 · Runpod · placement · creds     │ ◄──────────┤ <batch_id>/_done.json    │
 │ Local state: SQLite in user data dir         │  download  └────────────▲─────────────┘
 └──────────────┬───────────────────────────────┘                         │ stream in/out
                │ REST v2: create pod (queue = deploy when available)     │
                ▼                                                         │
 Runpod pod (RTX A5000, public Docker Hub image) ─────────────────────────┘
   supervisor (PID 1) ─► worker process: discover ready batches → transcribe → upload
   watchdog + always-terminate; backup copy of results on the Global Volume
 * MCP: not built now; the boundary is designed for it.
```

## 2. Repository layout (uv workspace)

```
pyproject.toml            workspace root: dev tools, ruff/mypy/pytest config (no runtime code)
.flake8, whitelist.txt    flake8 + plugins config, spellcheck whitelist
packages/contracts/       transcribe_contracts: models shared by desktop and worker
apps/desktop/             transcribe_ui: backend + frontends, PyInstaller spec
apps/worker/              transcribe_worker: Runpod worker, Dockerfile
scripts/                  dev tasks (parallel local checks)
docs/human/, docs/ai/     see CLAUDE.md
```

- Python **3.13** for every member (A8). 3.14 is supported by PySide6/PyInstaller, but not yet
  by the whole worker stack (WhisperX requires `<3.14`); one version everywhere keeps the shared
  contracts package simple.
- Code layout (A14), Django-like: each app and sub-package keeps its constants in
  `constants.py` (one short comment per constant), Pydantic models in `classes.py`, SQLAlchemy
  models in `models.py`; Alembic migrations live in `apps/desktop/.../migrations/`. Logic
  modules (`services.py` and similar) import from these.
- `transcribe_contracts` is the only code shared between the two apps. It holds the R2 object
  layout, the batch manifest, the result models and the schema version. Both apps pin the same
  workspace version, so a contract change is one PR that touches both sides and their tests.

## 3. Desktop app

### 3.1 Backend / frontend boundary

- The backend exposes one façade: `Backend.execute(request: Request, progress: ProgressSink) ->
  Response`. `Request` is a Pydantic discriminated union on `action` (for example
  `scan_tree`, `preview_selection`, `create_batch`, `review_batch`, `run_upload`, `launch_worker`,
  `refresh_status`, `plan_download`, `run_download`, `cleanup_cloud`, `export_custom_plan`,
  `import_custom_plan`). Each request type has a matching response model.
- Frontends only build requests and render responses/progress events:
  - **CLI**: `transcribe-ui run request.json` validates the file into `Request`, prints the
    `Response` as JSON; progress events go to stderr as NDJSON. `transcribe-ui schema` prints
    the JSON Schema of `Request` so requests can be written by hand or by an LLM.
  - **GUI**: view-models translate widget state into the same request models.
  - **MCP (later)**: one FastMCP tool per request type; the tool signature is the request
    model, so no new business logic is needed.
- Determinism: clock, ULID generator, filesystem root and external clients (R2, Runpod,
  ffmpeg) are injected ports. Given the same request and the same port fakes, the backend
  returns byte-identical JSON regardless of frontend. Flow tests assert this directly
  (section 7).
- Requests are validated only against machine-independent rules (for example the full Whisper
  language set, not the locally enabled languages), so the same request JSON is accepted or
  rejected identically everywhere. Local settings only shape what the GUI offers.
- Long operations (upload, download) are resumable commands: re-sending the same request
  continues from persisted state instead of starting over.

### 3.2 Discovery and selection

- Recursive scan from a chosen root with `os.scandir` (no symlink following, hidden/system
  entries skipped (Q19)), classified by extension into video/audio (Q18). No ffprobe during
  the scan: tens of thousands of files must list in seconds. ffprobe runs only on selected
  files, in the review step (§3.3). Probe results are cached in SQLite by path + size + mtime.
- Existing subtitles in the main view: sidecar files next to the media (`<stem>.srt`,
  `<stem>.<lang>.srt`, `<stem>.<lang>.forced.srt`, also `.vtt/.ass/.ssa/.sub`), language parsed
  from ISO 639-1/2 codes and common names (Q2, Q3). A file without a language code counts as
  "unknown language". Embedded subtitles are handled in the review step (Q2).
- Languages: the global settings list every Whisper language with an on/off switch (`hu`, `en`
  on by default); GUI choosers and filters offer only enabled languages (Q9, Q10). Languages
  without a WhisperX alignment model are marked there (A2).
- The scan result is a tree model; folder selection is tri-state and selects only the
  descendants that match the active filters (has subtitle any/specific languages/missing
  language, audio/video, name starts/ends/contains/regex) (Q9). Filters are part of the
  request, so the CLI and GUI select identically.
- The GUI uses a lazy `QAbstractItemModel` over the scan result (virtual rows, parent check
  state derived from children), which scales to tens of thousands of nodes.

### 3.3 Batches

- **Batch id**: `batch_YYYYMMDD_HHmmss` (local time of creation; on collision a `_2`, `_3`
  suffix). It is also the R2 prefix.
- **One batch per selection (A1)**: each item carries its target languages; its audio is
  converted and uploaded once and transcribed once per language. The worker orders the work by
  language so each per-language alignment model is loaded once (§5.3).
- **Item id**: each source file gets a ULID; its audio object is stored under that name. An
  item has one or more **jobs**. A job has a target language and a kind: `transcribe` (mode
  `normal|careful`, pointing to the audio track it uses) or `extract` (the embedded text
  subtitle stream with a given index is converted to SRT locally; nothing is uploaded, Q2).
  An item has at most one job per language, whatever its kind. `extract` jobs are local only:
  they never appear in the manifest, and their SRT (small) is stored in SQLite until placement.
  Usually all transcribe
  jobs share one track and one object; a file whose languages need different tracks (Q1, e.g.
  Hungarian dub + English original) gets one object per track (`<ulid>.a<track>.<ext>`).
- **Review step** (after selection, before upload; Q1, Q2): the selected files are probed with
  ffprobe, then the user resolves two summary views, each with the main view's language and
  folder filters: (1) files with embedded subtitles: transcribe or extract, per file and
  language (bitmap subtitles such as PGS/VobSub can only be transcribed); (2) files with more
  than one audio track: which track per file and language (pre-selected: the track tagged with
  the job's language, otherwise the default-flagged track, otherwise the first).
- **Phases** (persisted per batch and per item; every transition is idempotent, so any step can
  be interrupted and resumed):

  | Phase | Meaning | Exit condition |
  |---|---|---|
  | `composing` | selection being edited locally | user creates the batch |
  | `reviewing` | probe selected files; embedded-subtitle and track choices (review step) | every choice resolved |
  | `uploading` | per item: check source unchanged (size + mtime, else back to `reviewing` for that item) → run `extract` jobs → convert to temp → upload → verify (HEAD size) → delete temp | all audio objects uploaded and verified, all extractions stored |
  | `ready` | manifest uploaded to `batch_metadata/<id>.json` | worker writes `_done.json` |
  | `transcribed` | results available in R2 | user downloads |
  | `downloaded` | subtitles (and extracted subtitles) placed locally | user cleans up the cloud (optional) |
  | `cleaned` | all R2 data of the batch deleted (Q14) | terminal |

  Extracted subtitles are placed together with the transcribed ones. A batch with only
  `extract` jobs goes from `uploading` straight to placement (`transcribed` without a manifest).
  Cloud cleanup can also happen from `ready` or `transcribed` (below); a separate flag records
  "audio deleted".

  Items that failed or produced no speech are flagged in `transcribed`/`downloaded` and can be
  sent to a new retry batch (Q12, Q13) whose jobs use mode `careful`. The retry batch copies
  the existing audio objects server-side (S3 `CopyObject`) instead of re-uploading.
- **Cloud cleanup** (Q14): `cleanup_cloud` with scope `audio` or `everything` and an explicit
  confirmation flag; a maintenance action on the batch page, available at any time (offered
  after download). Deleting what is already gone is not an error.
  - Both scopes are refused while a worker run is active (a pod exists, or a `status.json`
    younger than its heartbeat timeout names the batch), so no worker writes after a cleanup.
  - `everything` order (resumable): manifest first (so no worker can pick the batch up again),
    then `_done.json` and results, then audio; a crash in between leaves only objects under the
    batch prefix, which the next attempt removes. If results were not downloaded yet, the
    response asks for a second confirmation (only the Global Volume backup would remain).
  - `audio` is refused while the batch has jobs without a result (the worker would fail them);
    after a crashed run, `everything` is the way out. Without audio, Retry (Q13) is no longer
    possible; the UI says so before confirming.
- **Readiness marker**: the manifest is uploaded last, after every audio object is verified.
  A manifest present in `batch_metadata/` therefore always means "ready"; the worker never sees
  half-uploaded batches.
- **Local state**: SQLite (WAL mode, single file) in `platformdirs.user_data_dir("Transcribe-UI")`
  through SQLAlchemy 2.0 ORM; Alembic migrations run at start-up, after taking the writer lock
  (A10). `alembic.ini`, `script.py.mako` and `versions/` are bundled as PyInstaller data;
  migrations use `render_as_batch=True` (SQLite ALTER); a database newer than the app is refused.
  One writer process at a time (lock file). Rows store Pydantic models as JSON plus indexed status columns. Temp audio
  goes to `user_cache_dir`, one file at a time. History is kept; finished batches can be hidden
  (Q21).

### 3.4 Conversion

- Audio files are uploaded as-is (no re-encoding, original extension kept in the manifest).
- Video files: the audio stream is stream-copied, never re-encoded:
  `ffmpeg -nostdin -i <in> -map 0:a:<n> -c copy -vn -sn -dn -map_metadata -1 <tmp>.mka`.
  Matroska audio accepts practically every codec, which fixes STTCLI's hard-coded `.m4a`
  (that only worked for AAC). If the muxer rejects a codec, fall back to lossless FLAC.
- Audio track choice for multi-track videos comes from the review step (Q1). Distinct tracks
  become distinct audio objects.
- Embedded subtitle extraction (Q2): `ffmpeg -nostdin -i <in> -map 0:s:<n> -c:s srt <tmp>.srt`
  for text codecs (`subrip`, `ass`, `ssa`, `mov_text`, `webvtt`, `text`).
- ffmpeg/ffprobe: bundled static LGPL builds inside the executable, overridable by a config
  path or `PATH` (A9).

### 3.5 Download and placement

Placement strategies (all produce a validated plan first, then execute it; conflicts are
reported before anything is written):

1. next to the original media file;
2. into a target folder mirroring the original tree (relative to the batch's `scan_root`,
   which the manifest stores);
3. into one flat folder;
4. into one flat folder, with chosen folder names joined by `_`: by default the X folders
   closest to the file (X = 1 initially; `C:\A\B\C\D\e.mkv`, X = 2 → `C_D_e.hu.srt`), or an
   explicit choice of folder levels (Q8);
5. custom: export a JSON plan (`source path`, `original folder`, `output folder`,
   `output file name`) for the user to fill in with an LLM, then import it. Import validation:
   schema, every item present exactly once, no unknown items, valid file names on the current
   OS (reserved names, forbidden characters, no `name:stream` alternate data streams), no
   duplicate targets, extension `.srt` (Q5), and every target, after resolving `..` and
   symlinks, inside the chosen output root.

Default output name `<stem>.<lang>.srt` (Q4, Q5). Existing target files are listed and skipped
unless the request says overwrite (Q6). Flat-mode name collisions are shown before download:
every colliding file gets its parent folder name as prefix (`A_movie.hu.srt`,
`B_movie.hu.srt`), adding levels until the names are unique (Q7). When no folder level separates
them (same folder, e.g. `movie.mkv` and `movie.mp4`, which also collide next to the media), the
source extension is added to the stem (`movie_mp4.hu.srt`) and the plan lists it.

Name rules for every strategy: X larger than the folder depth keeps every folder; characters
invalid on the target OS become `_`; a file name over 255 bytes is a plan error shown before
anything is written. Each subtitle is written to a temp file in the target folder and moved
into place with `os.replace`, so an interrupted download never leaves a partial file.

### 3.6 Credentials

- R2 access key/secret, R2 endpoint/bucket and the Runpod API key are stored with `keyring`
  (Windows Credential Locker, macOS Keychain, Linux Secret Service). Non-secret settings live in
  a JSON config file in the user config dir.
- Fallback when no keyring backend exists (headless Linux, WSL): environment variables, with
  a clear error message. Development uses an uncommitted `.env`.

### 3.7 Launching the worker

- Runpod **REST API v2** (`https://api.runpod.io/v2`; v1 was retired on 2026-11-15) through a
  thin typed `httpx` client (A4).
- `POST /v2/pods` with: `gpu {id: "NVIDIA RTX A5000", count: 1, minCudaVersion: "12.8"}`,
  `cloud: SECURE` (on-demand; spot no longer exists), `disk: 60`, the Global Volume mount
  (A5), pinned image tag, non-secret env, and `queue: {expiresAt: now + N h}` for **deploy when
  available**; the user sets the wait at every start, default 1 hour (Q17). A 202 response
  means "queued"; the app shows the queued state and can cancel it. Before starting, the app
  shows the total audio duration and an estimated cost range for confirmation (Q20). SSH uses
  Runpod's proxied basic SSH (A3), so no port or sshd is configured.
- Single worker at a time: `launch_worker` refuses while a pod or queued pod named
  `transcribe-ui-*` exists, so two workers never process the same batch (result writes are
  also conditional, see §4).
- The desktop app is a further line of defence against a forgotten pod: it stores the pod /
  queued-pod id, polls status and the worker's `status.json`, terminates at a deadline, and on
  startup reconciles `GET /v2/pods` and `GET /v2/queued-pods` and offers to terminate or cancel
  stragglers. This layer only works while the app is running; a queued pod may start while it
  is closed, which is why the pod-side layers (§5.2) must be sufficient on their own.
- Progress and cost shown in the app come from `runs/<run_id>/status.json` and the pod's `cost`
  field (USD/h) from the Runpod API.

## 4. R2 object layout (contract)

```
batch_metadata/<batch_id>.json            manifest, written by desktop only, last (= ready)
<batch_id>/audio/<ulid>[.a<track>].<ext>  input audio, written by desktop only
<batch_id>/results/<ulid>.<lang>.srt      subtitle, written by worker only (empty if no speech/failed)
<batch_id>/results/<ulid>.<lang>.json     job result (= done marker): status ok|no_speech|failed, error, timings,
                                          engine settings, aligned, detected language + probability (Q11)
<batch_id>/_done.json                     batch summary, written by worker after the last job
runs/<run_id>/status.json                 heartbeat + progress, overwritten by the worker every ~30 s
runs/<run_id>/worker.log                  worker log, uploaded periodically and before terminate
```

- Single-writer rule: every object has exactly one writer (desktop or worker), so no
  read-modify-write races exist.
- Result write order: `.srt` first, then `.json` with `If-None-Match: *` (R2 conditional PUT),
  so the JSON is the "done" marker and an existing result is never overwritten, even if two
  workers overlapped.
- Versioning: the manifest and results carry `schema_version`; each side refuses a major
  version it does not know (the worker records it in the run log, the desktop shows an error).
- The worker validates that each `audio_object_key` lies under `<batch_id>/audio/` and that
  `batch_id` matches the manifest file name.

Manifest: `schema_version`, `batch_id`, `created_at`, `scan_root` (+ `path_flavor`
`windows|posix`), `items[]`. Item: `id` (ULID), `original_path` (absolute), `source_size`,
`source_mtime_ns`, `media_kind`, `audio[]` (`object_key`, `track_index`, `codec`,
`duration_s`, `size_bytes`), `jobs[]` (`language`, `mode`, `audio_object_key`).

Paths are stored as text exactly as the OS reported them (no case or Unicode normalisation),
plus `path_flavor`. Names that are not valid Unicode (unpaired surrogates on Windows, non-UTF-8
bytes on POSIX) are rejected at scan time with a visible warning instead of being mangled.
Comparisons for sidecar matching use NFC on macOS and case-insensitive matching on Windows.

## 5. Worker (Runpod)

### 5.1 Image

- Base (A3): `nvidia/cuda:12.8.1-base-ubuntu24.04` + uv-managed Python 3.13 + the worker's
  locked dependencies (WhisperX 3.8.x, `torch~=2.8` cu128 from the PyTorch index) + system
  ffmpeg. cuBLAS/cuDNN 9 come from the torch wheels; `LD_LIBRARY_PATH` points CTranslate2 at
  them, and start-up asserts that CTranslate2 sees the GPU. No sshd (proxied basic SSH).
  The Runpod `cu1300`/`cu1320` images are out (CTranslate2 has no CUDA 13 wheels); the
  `cu1281-torch280` image fits but keeps Python/torch outside `uv.lock`.
- Baked models (A5): `large-v3` (CT2 fp16, 3.09 GB), the alignment models for `hu` (HF) and
  `en` (a torchaudio bundle, which lives in the torch hub cache, not the HF cache). `HF_HOME`
  and `TORCH_HOME` point to the same fixed paths at build time and run time. WhisperX 3.8
  defaults to pyannote VAD; the worker sets `vad_method="silero"` explicitly (Silero ships with
  the package). Alignment models for other enabled languages are downloaded at run time to the
  container disk.
- The `engine` extra pins `torch~=2.8.0` (WhisperX's pin) from the PyTorch cu128 index with a
  `sys_platform == 'linux'` marker, so the universal `uv.lock` still resolves on Windows/macOS.
- The CI image build frees runner disk space first (the image is roughly 10 GB).
- Published to public Docker Hub by CI with tags `sha-<short>`, `latest` and
  `schema-<contracts major>`. The desktop app uses the `schema-<major>` tag of the contracts
  version it was built with, so it always gets a compatible worker even when a desktop-only
  change did not rebuild the image (A12).

### 5.2 Process model

```
entry (PID 1 via tini, stdlib-only Python module, no app imports)
 ├─ 1. spawns the watchdog: a separate stdlib-only Python process that sleeps until
 │     MAX_RUNTIME_S (parsed with a safe default if invalid), then terminates the pod
 ├─ 2. runs `transcribe_worker.run` as a child process (imports, config, GPU check, pipeline)
 └─ 3. after the child exits for any reason: terminate own pod; with KEEP_POD_ALIVE=true
        (test mode) it sleeps instead, and the watchdog ends the pod after MAX_RUNTIME_S
MAX_RUNTIME_S: 2 h in test mode (A6); in production derived from the total audio duration
            with a margin, passed by the desktop app at launch
terminate = DELETE /v2/pods/$RUNPOD_POD_ID, retries with backoff; after a few failures also
            POST /v2/pods/{id}/action {"action":"stop"}; verified by GET. It never gives up:
            the entry and the watchdog keep retrying and never exit while the pod exists
```

The entry never exits on its own: an exiting PID 1 presumably makes Runpod restart the
container [I, to verify at the first approved run], which would start a fresh, billed worker and
reset the watchdog. Flow tests cover "terminate keeps failing".

Termination layers, each sufficient on its own for the failures it covers:

1. Entry `finally` after the child exits (normal end, crash, config error, missing GPU).
2. Watchdog process, independent of the entry and the child: covers hangs and a blocked entry.
3. Desktop app deadline and startup reconciliation (only while the app runs, §3.7).
4. External reaper (A17): a scheduled GitHub Actions workflow with a pods-only key terminates
   any `transcribe-ui-*` pod older than its deadline. Covers failures before Python starts
   (image pull failure, crash loop) and queued pods that start while the desktop app is closed.
5. Account spend limit and low-balance alert (a zero balance stops pods instead of
   terminating them, which still bills volume storage).

The entry module is deliberately tiny and stdlib-only, so an import error in the app cannot
prevent termination; a CI test runs it inside the built image against a fake API. Whether the
pod-scoped `RUNPOD_API_KEY` may delete its own pod must be tested (A15); if not, a separate
pods-only key is passed as a Runpod secret.

### 5.3 Pipeline

- Discover: list `batch_metadata/`, load manifests, skip batches with `_done.json`.
- Order (A1): all pending jobs of the run are sorted by language, then batch, then item, so the
  per-language alignment model is loaded once per language (the previous one is released and
  the CUDA cache emptied first); the Whisper model stays loaded.
- Audio of an item with several languages is needed in several passes: downloaded audio stays
  in a size-bounded cache on the container disk (least recently used evicted) and is
  downloaded again if evicted (R2 has no egress fee).
- The desktop app sees each job's result as soon as its JSON exists; `_done.json` appears only
  when the batch's last language pass is finished.
- Streaming, not "download everything first": a bounded prefetch queue downloads the next
  few audio files while the GPU transcribes the current one; each result is uploaded and
  backed up as soon as it is ready. At most the in-flight item is lost on interruption.
- Idempotent: a job whose result JSON already exists in R2 is skipped, so a restarted pod
  continues where the previous one stopped and successful results are never redone or
  overwritten (conditional PUT, §4).
- Per job: ffmpeg decodes to 16 kHz mono (robust for AC3/E-AC3/DTS/TrueHD, picks the stream
  explicitly) → language check (Whisper language detection on several 30 s speech windows
  spread over the file, majority vote, so a music intro does not decide; a mismatch with the
  chosen language is recorded, Q11 open) → WhisperX `large-v3`, fp16, forced language,
  batch 16, Silero VAD → wav2vec2 alignment (A2; skipped with `aligned: false` when the
  language has no model) → quality checks (repetition, compression ratio, empty output on
  non-silent audio) → on suspicion, or in `careful` mode, a re-run with stricter decoding
  (beam search, temperature fallback, `condition_on_previous_text=False`) → cue builder (max
  42 chars/line, 2 lines, 1–7 s, split at punctuation/pauses) → SRT. Per-job timeout; any
  exception marks the job `failed` and the run continues.
- Backup: each result file is also written to the Global Volume
  (`/workspace/backups/<batch_id>/...`) as a whole file (object-store semantics: no
  rename-based atomicity there, so write-once files only). Before transcribing a job, the
  worker checks the backup: if a result exists there but not in R2 (an earlier upload failed),
  it uploads the backup instead of transcribing again.
- After the last item of a batch: write `_done.json`; after the last batch: exit 0, and the
  supervisor terminates the pod.

### 5.4 Credentials in the pod (A7)

Proposed: R2 bucket-scoped token stored as Runpod Secrets, referenced from a Runpod template
that the desktop app creates/updates via API; the image never contains credentials.
Hardening option for later: presigned URLs in a per-run job object, so the pod holds no
long-lived R2 key.

## 6. Cross-cutting

- Typing: mypy strict with the pydantic plugin; Pydantic v2 models are frozen where possible.
- Errors: domain exceptions per module; frontends map them to error responses
  (`{"error": {"code": ..., "message": ..., "details": ...}}`), never stack traces.
- Logging: stdlib `logging`; JSON lines in the worker (uploaded per run), human-readable in the
  desktop app plus a rotating file in the user log dir. Secrets are never logged; the worker
  never logs its environment (it contains `RUNPOD_API_KEY` and the R2 secrets).
- R2 client: boto3 with `request_checksum_calculation="when_required"` and
  `response_checksum_validation="when_required"` (botocore ≥ 1.36 default checksums are not
  reliably supported by R2), region `auto`, multipart ≥ 64 MiB parts.

## 7. Testing strategy

Few unit tests; mostly regression and flow tests:

- **Contract tests** (`packages/contracts`): JSON Schema snapshots of the manifest, results and
  `Request`/`Response` unions; any change is a visible diff.
- **Desktop flow tests**: a tiny media tree is generated at test time with ffmpeg `lavfi`
  sources (multi-track video, AAC/Opus/AC3/PCM audio, existing sidecar subtitles); S3 is
  `moto`; Runpod is an `httpx` mock. Flows: scan → filter → select → create batch → convert +
  upload (interrupted and resumed) → simulated worker results → every placement strategy →
  cleanup. Each flow runs the same request JSON through the CLI entry point and through the
  backend directly and asserts identical responses (golden files).
- **Worker flow tests**: fake transcriber behind the engine protocol + `moto` (the WhisperX
  stack is an optional extra, not installed in CI): discovery, language ordering,
  prefetch pipeline, resume after a kill, failure isolation (one failing file, one empty
  file), `_done.json`, and the supervisor always calling terminate (child crash, hang →
  watchdog, normal exit; `KEEP_POD_ALIVE`).
- **GPU smoke test** (marker `gpu`, only with the architect present, A15): real WhisperX on a
  short Hungarian and English clip on the pod, started from an interactive SSH session (the
  proxied SSH has no SCP/SFTP; `POST /v2/pods/{id}/exec` is the non-interactive alternative).
- Markers: `integration` (real R2/Runpod) and `gpu` (needs the GPU stack); both are deselected
  by default and in CI.
- **GUI**: pytest-qt smoke tests and view-model → request mapping tests.

## 8. CI/CD (summary; details in `tooling.md`)

- `ci.yml` runs on every pull request and push to `main` (no workflow-level path filter, so
  required checks always report). A first job detects which members changed; lint, typecheck
  and test jobs per member run only when needed; one aggregate `ci-ok` job is the single
  required status check. gitlint checks PR commits.
- `release-desktop.yml` on push to `main` when desktop/contracts code changes: PyInstaller
  one-file builds on Windows, macOS arm64, macOS x86_64 and Ubuntu 22.04, smoke-run
  `--version`, upload only the executable (`actions/upload-artifact` with `archive: false`)
  and attach the four executables to a GitHub Release (A13).
- `release-worker.yml` on push to `main` when worker/contracts code changes: build the Docker
  image, run the entry/termination smoke test inside it, push to Docker Hub (push waits for
  A12).
- `reaper.yml` on a schedule (A17): terminate overdue `transcribe-ui-*` pods.
- No release workflow runs on pull requests.
