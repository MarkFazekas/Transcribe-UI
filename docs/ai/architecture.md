# Architecture

Status: **approved design**. Requirements and human decisions: `docs/human/requirements.md`.
Open questions: `questions-for-architect.md`, `questions-for-product.md` (open items here say
so). Research behind the decisions: `docs/ai/research/`.

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
apps/worker/              transcribe_worker: Runpod worker, Dockerfile; also the pod reaper
scripts/                  dev tasks (parallel local checks)
docs/human/, docs/ai/     see CLAUDE.md
```

- Python **3.13** for every member. 3.14 is supported by PySide6/PyInstaller, but not yet
  by the whole worker stack (WhisperX requires `<3.14`); one version everywhere keeps the shared
  contracts package simple.
- Code layout, Django-like: each app and sub-package keeps its constants in
  `constants.py` (one short comment per constant), Pydantic models in `classes.py`, SQLAlchemy
  models in `models.py`; Alembic migrations live in `apps/desktop/.../migrations/`. Logic
  modules (`services.py` and similar) import from these.
- `transcribe_contracts` is the only code shared between the two apps. It holds the R2 object
  layout, the batch manifest, the result models, the schema version and the pod name
  convention. Both apps pin the same workspace version, so a contract change is one PR that
  touches both sides and their tests.

## 3. Desktop app

### 3.1 Backend / frontend boundary

- The backend exposes one façade: `Backend.execute(request: Request, progress: ProgressSink,
  cancel: CancelToken) -> Response` (`execute_json` validates a JSON document first; an invalid
  one is answered with the error `invalid_request`). `Request` is a Pydantic discriminated union
  on `action` (for example `get_app_info`, `scan_tree`, `preview_selection`, `create_batch`,
  `review_batch`, `run_upload`, `launch_worker`, `refresh_status`, `plan_download`,
  `run_download`, `cleanup_cloud`, `export_custom_plan`, `import_custom_plan`). `Response` is a
  union discriminated on `status`:
  - `ok`: the request's own response model (discriminated on `action`);
  - `needs_confirmation`: nothing was changed; it lists the request fields to set
    (confirmations are data, so GUI, CLI and MCP behave the same);
  - `error`: `{"status": "error", "action": ..., "error": {"code", "message", "details"}}`
    (§6). Handlers raise `DomainError(code, ...)`; an unexpected exception becomes
    `internal_error` with a generic message (the traceback goes to the log only).
- Models live in the desktop app (not in contracts: they are not shared with the worker): the
  unions in `transcribe_ui/api/classes.py`, each sub-package's request and response models in
  its own `classes.py`, built on `api/base.py` (`FrozenModel`, `LocalPath`, `PathFlavor`), so
  `api/classes.py` can import them without a cycle. A path on the user's computer is typed
  `LocalPath`, the host's path flavour `PathFlavor`; golden-file tests normalise exactly these
  fields (§7).
- The dispatch table (`api/backend.py`) maps each action to its handler and marks whether it
  needs the database; request types that do not (app info, settings, credentials, scans) never
  open it, so the CLI can run them while the GUI holds the writer lock.
- Cancellation: long requests check the `CancelToken` between items and stop with the error
  `cancelled` after persisting what is done; re-sending the request resumes.
- Frontends only build requests and render responses/progress events:
  - **CLI** (argparse): `transcribe-ui run request.json` validates the file into `Request`,
    prints the `Response` as indented JSON; progress events go to stderr as NDJSON; both UTF-8
    with `\n` line ends on every OS. Nothing else reaches stderr (log records and warnings go to
    the log file, §3.8), and stdout gets a JSON response whatever fails: an
    unexpected exception outside the backend gives `internal_error` (exit 1), a Ctrl+C before a
    request was read gives `cancelled` (exit 130). The one exception is `--help`/`-h`: argparse's
    usage text, exit 0. The request file must be UTF-8; a UTF-8 byte order mark is stripped,
    and a UTF-16 file (PowerShell 5.1 `>` / `Out-File`) is refused with `invalid_request`
    ("must be UTF-8"). Ctrl+C sets the cancel token, a second Ctrl+C stops at
    once (every step is idempotent, so the state is consistent either way). Exit codes: 0
    success, 1 error response, 2 request file (or command line) invalid, 3
    `needs_confirmation`, 130 `cancelled`. `transcribe-ui schema` prints the JSON Schema of
    `Request` (`--response`: of `Response`) so requests can be written by hand or by an LLM;
    `--version` prints the app version.
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

- Two requests in `discovery/`, both without the database: `scan_tree` (the media tree below a
  root) and `preview_selection` (resolves a selection on a scan).
- Scan cache: each scan gets a `scan_id` (from the injected id source), and the backend keeps
  the latest 4 scans in process (`discovery/cache.py`, `Ports.scan_cache`) with the options
  they were made with. `preview_selection` takes an optional `scan_id`: when that scan is kept
  and was made with the same options, it is used as it is (the GUI previews on every click,
  and the preview stays consistent with the tree on screen); otherwise the request scans again
  and keeps the new scan. The response names the scan it used. A one-shot CLI process starts
  empty, so its previews always scan; the answer differs only if the disk changed in between.
- Scan options are request fields with defaults (`discovery/constants.py`): the video and audio
  extension lists (lower case, no dot, compared case-insensitively; the two lists are disjoint
  and never contain a subtitle extension) and `include_hidden`. The GUI fills them from the
  settings; request handling never reads the settings file.
- The root must be an absolute path of an existing, listable folder (`root_not_absolute`,
  `root_not_found`, `root_not_a_folder`, `root_unreadable`); it is reported after
  `os.path.normpath`. A symlinked
  root is followed (the user chose it).
- Walk: `os.scandir` without following links, iterative (an explicit stack, so no recursion
  limit). Symlinks and Windows junctions are reported (`link_skipped`) and skipped, so loops
  cannot occur; folders deeper than 256 levels give `too_deep`. Entries are classified by
  their directory-entry type and name first; `stat` runs only for media files (size, mtime)
  and, where the OS keeps hidden flags there, for each entry (free on Windows; macOS/BSD
  `st_flags`). Hidden entries (a name starting with `.`, the Windows hidden attribute alone or
  with system, the macOS `UF_HIDDEN` flag) are skipped unless `include_hidden`; a system-only
  entry stays visible. macOS AppleDouble files (`._` prefix) are never media or sidecars, even
  with `include_hidden`. An unreadable folder or entry gives `unreadable`; an entry that
  vanishes during the scan is skipped silently. A name that is not valid Unicode (undecodable
  bytes on POSIX, lone surrogates on Windows) cannot be shown or stored: it is reported as
  `invalid_name` with U+FFFD in its place and skipped. Warnings never stop the scan. The cancel
  token is checked per folder; a progress event goes out every 1000 media files.
- No ffprobe during the scan: tens of thousands of files must list in seconds. ffprobe runs
  only on selected files, in the review step (§3.3). Probe results are cached in SQLite by
  path + size + mtime; the scan reports size and `mtime_ns` of each file.
- Response shape: flat lists, so deep trees do not nest JSON deeply and the GUI's lazy model
  indexes them directly. `folders` in depth-first order (index 0 is the root, each with its
  parent's index), `files` grouped by folder in that order, each with its folder's index;
  folders without media at any depth are left out. Siblings are ordered by their composed
  (NFC), case-folded name, then the exact name: the same order on every OS.
- Sidecar subtitles (`srt`, `vtt`, `ass`, `ssa`, `sub`; not configurable): a subtitle file
  belongs to the media files whose stem is the longest `.`-bounded prefix of its name
  (`Movie.part2.en.srt` belongs to `Movie.part2.mkv`, not `Movie.mkv`); media files sharing
  that stem (`Movie.mkv`, `Movie.mp4`) all get it. When the stem is shorter than the name, the
  next token must be a language, `forced` or a known tag (`sdh`, `cc`, `hi`, `default`,
  `full`, `cd1`, `cd2`, ...); otherwise the subtitle belongs to no media file
  (`Show.S01E02.en.srt` is not `Show.mkv`'s). A subtitle of no media file is ignored, and a
  dot-prefixed one never matches. The remaining name tokens give the language (the first token
  that names one) and the `forced` flag (a `forced` token anywhere); other tokens are ignored. Languages recognised: Whisper's codes and
  English names, ISO 639-2/B and /T codes (and the 639-3 codes `cmn`, `fil`, `nob`, `nno`,
  `yue`), a few other spellings (`iw`, `jv`, `nb`, `farsi`, ...) and a code with a region or
  script suffix (`pt-BR`, `en_US`, `zh-Hans`). Anything else, including real languages Whisper
  does not support, is "unknown language" (`language: null`). Embedded subtitles are handled in
  the review step.
- Path-flavour rule (`discovery/rules.py`, injected as `Ports.path_rules`, so the Windows and
  macOS rules are tested on Linux): Windows compares names case-insensitively by simple
  one-to-one upper-casing per character, as NTFS does (`ß` stays `ß`), and splits paths on
  `\` and `/`; macOS compares with full Unicode case folding and normalisation-insensitively
  (APFS and HFS+ defaults); other POSIX systems compare exactly. The rule shapes comparisons
  only (sidecar stems, selection paths); reported names and paths stay as the OS returned them.
  The response's `path_flavor` is the rule's flavour. Limitation: the rule is per OS, not per
  volume, so a case-sensitive macOS volume or a Windows folder with per-directory case
  sensitivity (WSL) can match two files that differ only in case as one.
- Languages: the global settings list every Whisper language with an on/off switch (`hu`, `en`
  on by default); GUI choosers and filters offer only enabled languages. Languages
  without a WhisperX alignment model are marked there.
- Selection is an ordered list of operations, each `include` or `exclude` with a path (folder
  or file) and the filters active when it was made; it applies to the media files at or below
  the path that match those filters, and later operations override earlier ones. This gives
  the GUI's tri-state folder selection: selecting a folder selects only the descendants that
  match the active filters. Filters (all given parts must match): media kind (any, audio,
  video); subtitle languages with the rule all / any / `missing_one` (lacks at least one),
  counting only sidecars that are not forced; name of the file without its extension
  (`starts_with`, `ends_with`, `contains`, compared NFC and case-folded; `regex`, searched in the
  NFC name ignoring case). Requests are validated machine-independently (languages against the
  full Whisper set, the regex compiled and at most 200 characters). The cancel token is checked
  per file while filters run; a single regex match cannot be interrupted (relevant for the MCP
  frontend, whose requests come from a model). An operation path that is relative, contains
  `..` or is outside the root is `path_outside_root` with the operation's index in the details
  (not the path: details are not path-normalised, and the request holds it); one with no media
  file at or below it gives the warning `no_media_at_path`. Paths are matched by binary search
  over the sorted file keys.
- `preview_selection` returns the selected files in scan order (with their sidecar languages,
  forced ones and unknown-language count), each folder with a selected file below it as `all`
  or `partial`, and totals (files, video, audio, bytes).
- The GUI uses a lazy `QAbstractItemModel` over the scan result (virtual rows, parent check
  state derived from children), which scales to tens of thousands of nodes.

### 3.3 Batches

- **Batch id**: `batch_YYYYMMDD_HHmmss` (local time of creation; on collision a `_2`, `_3`
  suffix). It is also the R2 prefix.
- **One batch per selection**: each item carries its target languages; its audio is
  converted and uploaded once and transcribed once per language. The worker orders the work by
  language so each per-language alignment model is loaded once (§5.3).
- **Item id**: each source file gets a ULID; its audio object is stored under that name. An
  item has one or more **jobs**. A job has a target language and a kind: `transcribe` (mode
  `normal|careful`, pointing to the audio track it uses) or `extract` (the embedded text
  subtitle stream with a given index is converted to SRT locally; nothing is uploaded).
  An item has at most one job per language, whatever its kind. `extract` jobs are local only:
  they never appear in the manifest, and their SRT (small) is stored in SQLite until placement.
  Usually all transcribe jobs share one track and one object; a file whose languages need different tracks (e.g.
  Hungarian dub + English original) gets one object per track (`<ulid>.a<track>.<ext>`).
- **Review step** (after selection, before upload): the selected files are probed with
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
  | `cleaned` | all R2 data of the batch deleted | terminal |

  Extracted subtitles are placed together with the transcribed ones. A batch with only
  `extract` jobs goes from `uploading` straight to placement (`transcribed` without a manifest).
  The phases are not a strict line (planned on the proposed defaults of Q24-Q27, `plans/m5.md` §1):
  a batch in `composing` or `reviewing` can be deleted; finished subtitles can already be
  downloaded in `ready` and again in `downloaded` (extracted ones even in `cleaned`); a batch
  reaches `downloaded` once it has `_done.json` (or is extract-only) and every job has a placement
  outcome (placed, or skipped as an existing file, a missing folder or an empty result), in one
  download or several; an item whose source changed during `uploading` goes back to review on its
  own (or is excluded) while the batch stays `uploading` (excluding the last item is refused); a
  batch whose upload completes while a run is active keeps its manifest back and stays `uploading`
  until the run ends (the held manifest is rebuilt from the database when it is published), so a
  worker never gets work its deadline and estimate did not include; cloud cleanup is possible from
  `uploading` on (below). `audio` cleanup sets an "audio deleted" flag without changing the phase;
  `everything` ends in `cleaned`.

  Items that failed or produced no speech are flagged in `transcribed`/`downloaded` and can be
  sent to a new retry batch whose jobs use mode `careful` and the same language. The retry
  batch copies the existing audio objects server-side (S3 `CopyObject`) instead of
  re-uploading; it is created in `uploading`, idempotent by source batch and job set, and
  finished through the resumable upload path. Their (empty) subtitles are not placed unless
  the download request asks for empty files.
- **Existing languages**: a job for a language the file already has a subtitle in is allowed;
  `create_batch` returns a warning listing such files. Folder selection with the filter
  "missing <language>" does not select them in the first place.
- **Cloud cleanup**: `cleanup_cloud` with scope `audio` or `everything` and an explicit
  confirmation flag; a maintenance action on the batch page, available at any time (offered
  after download). Deleting what is already gone is not an error.
  - Both scopes are refused while a worker run is active (a local run launching, queued or
    running; a live `transcribe-ui-*` pod or queued pod; or a `status.json` younger than its
    heartbeat timeout; `plans/m5.md` §1), so no worker writes after a cleanup.
    A batch still in `uploading` has no manifest, so no worker sees it: its cleanup
    (`everything` only, Q26) is allowed during a run and also aborts open multipart uploads.
  - `everything` order (resumable): manifest first (so no worker can pick the batch up again),
    then `_done.json` and results, then audio; a crash in between leaves only objects under the
    batch prefix, which the next attempt removes. If results were not downloaded yet, the
    response asks for a second confirmation (only the Global Volume backup would remain).
  - `audio` is refused while the batch has jobs without a result (the worker would fail them);
    after a crashed run, `everything` is the way out. Without audio, Retry is no longer
    possible (a retry request is refused); the UI says so before confirming.
  - Both scopes are refused on a batch while one of its retry batches is still `uploading`
    with copies pending (the error names the retry batch).
- **Readiness marker**: the manifest is uploaded last, after every audio object is verified.
  A manifest present in `batch_metadata/` therefore always means "ready"; the worker never sees
  half-uploaded batches.
- **Local state**: SQLite (WAL mode, single file) in the user data dir through SQLAlchemy 2.0
  ORM, one writer process at a time; rows store Pydantic models as JSON plus indexed status
  columns. Temp audio goes to `user_cache_dir`, one file at a time. History is kept; finished
  batches can be hidden. Details: §3.8.

### 3.4 Conversion

- Audio files are uploaded as-is (no re-encoding, original extension kept in the manifest).
- Video files: the audio stream is stream-copied, never re-encoded:
  `ffmpeg -nostdin -i <in> -map 0:a:<n> -c copy -vn -sn -dn -map_metadata -1 <tmp>.mka`.
  Matroska audio accepts practically every codec, which fixes STTCLI's hard-coded `.m4a`
  (that only worked for AAC). If the muxer rejects a codec, fall back to lossless FLAC.
- Audio track choice for multi-track videos comes from the review step. Distinct tracks
  become distinct audio objects.
- Embedded subtitle extraction:
  `ffmpeg -nostdin [-copyts] -i <in> -map 0:s:<n> -c:s srt -f srt pipe:1` for text codecs
  (`subrip`, `ass`, `ssa`, `mov_text`, `webvtt`, `text`); the SRT is read from stdout (no temp
  file). The file is probed first for its container start time. Up to 0.5 s (audio priming
  gives tens of milliseconds) `-copyts` keeps the cue times as stored, which without it would
  run late by that start; a later start (MPEG-TS and M2TS often begin at a second or more) is
  subtracted (ffmpeg's default without `-copyts`), since players count from the start of the
  file. Bitmap codecs (`hdmv_pgs_subtitle`, `dvd_subtitle`, `dvb_subtitle`, `xsub`) and unknown
  ones are transcribe-only.
- ffmpeg/ffprobe: bundled static LGPL builds inside the executable. Resolution order: the
  path set in the settings, then the bundled build (`sys._MEIPASS` of a PyInstaller build),
  then `PATH` (development runs without a bundle), so a `PATH` ffmpeg of unknown version never
  silently replaces the tested one. A settings path that is not a file is an error
  (`media_tool_not_found`), never skipped. The `PATH` search is the app's own: only absolute
  entries, never the current folder (Windows' `shutil.which` searches it first), `PATHEXT` on
  Windows. The tools are resolved when first used, so requests that need no media work without
  them.
- Media adapter (`transcribe_ui/media/`): the `MediaTools` port (`probe`, `convert`,
  `extract`), its ffmpeg adapter and a deterministic fake for golden flows. Processes run
  through an injectable `ProcessRunner`:
  - every ffmpeg/ffprobe process starts in its own process group, so the terminal's Ctrl+C
    reaches only the app; the runner polls the cancel token and the timeout every 0.1 s and
    kills the process itself, also when anything else interrupts the wait (a second Ctrl+C),
    together with everything it started (a package-manager shim such as Chocolatey's
    `ffmpeg.exe` runs the real ffmpeg as its own child):
    - POSIX: a new session (`start_new_session`; no `preexec_fn`, which is not thread-safe);
      the kill is `killpg(SIGKILL)`. If the app itself dies, a running ffmpeg is orphaned and
      runs to its end; this is accepted, since it writes only its own uniquely named partial
      file, which the start-up cleanup of the temp folder (M5.10) removes;
    - Windows: `CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`, created suspended, put into a
      job object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, then resumed (ctypes, no
      dependency); the kill is `TerminateJobObject`, and when the app dies the OS closes the
      job and kills the tree;
  - timeouts (hang guards, not performance limits): probe 2 min; each conversion attempt and
    an extraction 1 h or, for a large file, as long as reading it at 2 MB/s takes;
  - a conversion writes `<name>.<ext>.<random>.part` (unique per attempt, so an orphaned ffmpeg
    can never write into a file a later attempt renames) and renames it into place on success
    (retried for up to 2 s on a Windows sharing violation); the partial file is removed on
    failure, timeout and cancel, so a finished name always means a complete file. The FLAC
    fallback runs whenever the stream copy exits non-zero (not after a timeout or cancel),
    since ffmpeg's wording of a muxer refusal is not stable across versions;
  - errors: `media_tool_not_found` (details `source`: `settings`, `search` or `start`),
    `media_tool_failed` (details: tool, operation, exit code and the last 500 characters of
    stderr), `media_tool_timeout`, `media_output_unwritable` (the output folder or file cannot
    be written), `media_index_invalid` (a negative track or stream index; nothing runs),
    `cancelled`.

### 3.5 Download and placement

Placement strategies (all produce a validated plan first, then execute it; conflicts are
reported before anything is written):

1. next to the original media file;
2. into a target folder mirroring the original tree (relative to the batch's `scan_root`,
   which the manifest stores);
3. into one flat folder;
4. into one flat folder, with chosen folder names joined by `_`: by default the X folders
   closest to the file (X = 1 initially; `C:\A\B\C\D\e.mkv`, X = 2 → `C_D_e.hu.srt`), or an
   explicit choice of folder levels;
5. custom: export a JSON plan (`source path`, `original folder`, `output folder`,
   `output file name`) for the user to fill in with an LLM, then import it. Import validation:
   schema, every item present exactly once, no unknown items, valid file names on the current
   OS (reserved names, forbidden characters, no `name:stream` alternate data streams), no
   duplicate targets, extension `.srt`, and every target, after resolving `..` and
   symlinks, inside the chosen output root.

Default output name `<stem>.<lang>.srt`. Existing target files are listed and skipped
unless the request says overwrite. Flat-mode name collisions are shown before download:
every colliding file gets its parent folder name as prefix (`A_movie.hu.srt`,
`B_movie.hu.srt`), adding levels until the names are unique. When no folder level separates
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
  thin typed `httpx` client.
- `POST /v2/pods` with: `gpu {id: "NVIDIA RTX A5000", count: 1, minCudaVersion: "12.8"}` (excludes
  A5000 hosts that report CUDA 12.4), `cloud: SECURE` (on-demand; spot no longer exists),
  `disk: 60`, the Global Volume mount, the template (which alone pins the image tag), non-secret
  env, and `queue: {expiresAt: now + N h}` for **deploy when available**; the user sets the
  wait at every start, 5 min to 24 h (open A19), default 1 hour. A 202 response means "queued";
  the app shows the queued state and can cancel it (a queued pod that deploys during the cancel
  is terminated; the run counts as cancelled only once that is verified). Before starting, the
  app shows the total audio duration and an estimated cost range for confirmation; there is no
  in-app spending limit (the account's own limit applies). SSH uses Runpod's proxied basic SSH,
  so no port or sshd is configured.
- Pod name (`transcribe_contracts.pods`): `transcribe-ui-<run_id>-until-<YYYYMMDDTHHMMZ>`,
  the run's ULID and a UTC deadline rounded up to the whole minute (61 characters; the v2 spec
  sets no maximum, the builder keeps to 63). A queued pod carries the same name as the pod it
  deploys. The deadline is the moment after which nothing legitimate can still be running:
  for a direct launch, launch time + start-up margin (image pull, model load) +
  `MAX_RUNTIME_S` + margin; for a queued launch, the queue's `expiresAt` + start-up margin +
  `MAX_RUNTIME_S` + margin, because the pod may deploy just before the queue expires. The
  external reaper (§5.2, layer 4) relies on this name alone; the parser returns nothing for
  any other shape. A deadline must lie at most 32 days after the creation of the pod or
  queued pod (the reaper's plausibility cap, §5.2).
- Single worker at a time: `launch_worker` refuses while a live pod (any status but
  `TERMINATED`) or a `QUEUED` queued pod named `transcribe-ui-*` exists, so two workers never
  process the same batch (result writes are also conditional, see §4).
- Creating calls are never re-sent after an ambiguous failure (timeout, 5xx, connection drop
  after sending), since a lookup can miss a pod still being created. The run is recorded
  locally (run id, pod name, status `launching`) before the `POST /v2/pods`; after such a
  failure the app looks the name up in `GET /v2/pods` and `GET /v2/queued-pods` and adopts
  what it finds, otherwise the run stays `launching` and later status checks resolve it; it
  counts as failed only after a settling delay and two empty lookups. The cloud setup's
  template, secret and Global Volume creates stop the same way; a re-run looks them up by their
  fixed names and removes duplicates.
- The template carries no mounts (template mounts can only be persistent volumes); the Global
  Volume (`transcribe-ui-backups`) is looked up by name and mounted through `mounts.global`
  in the create body.
- The desktop app is a further line of defence against a forgotten pod: it stores the pod /
  queued-pod id, polls status and the worker's `status.json`, terminates at a deadline (first
  seen running + `MAX_RUNTIME_S` + a margin, capped by the pod name deadline), and on
  startup reconciles `GET /v2/pods` and `GET /v2/queued-pods` and offers to terminate or cancel
  stragglers. This layer only works while the app is running; a queued pod may start while it
  is closed, which is why the pod-side layers (§5.2) must be sufficient on their own.
- "Start transcription" launches one worker for every batch in `ready`. The worker processes
  every manifest in `batch_metadata/` without `_done.json`, so the estimate and
  `MAX_RUNTIME_S` cover all of them, including batches this computer does not know (listed
  separately, open Q28). The app shows:
  waiting for a machine (cancellable), starting, transcribing (batch, file, elapsed time,
  cost so far), finished or failed.
- Progress and cost shown in the app come from `runs/<run_id>/status.json` and the pod's `cost`
  field (USD/h) from the Runpod API. After a run has ended and its pod is gone, the app keeps
  the final status and the worker log locally and deletes `runs/<run_id>/`.

### 3.8 Local state, settings and concurrency

Code: `transcribe_ui/state/`. One `LocalState` per backend (in the injected `Ports`) holds the
directories, the settings store, the locks and the lazily opened database.

- **Directories**: `platformdirs` with app name `Transcribe-UI` (no author folder): data
  (`state.sqlite3`, `state.lock`), config (`settings.json`), cache (temp audio) and log (one
  file per frontend: `transcribe-ui-cli.log`, `transcribe-ui-gui.log`, later one for MCP). Tests and other injected roots use `<root>/{data,config,cache,log}`.
- **Schema**: every table lives in `state/models.py` (`batches`, `items`, `jobs`,
  `audio_objects`, `extracted_subtitles`, `probe_cache`, `runs`, `placements`): indexed status
  columns (phase, status, outcome, flags) plus a JSON `payload` column, so most later changes
  touch payload classes, not tables. Timestamps come from the injected clock and are stored as
  UTC. Constraint names follow a fixed naming convention (batch migrations find them by name).
  Until M5 is marked **(built)** the schema is one initial Alembic revision edited in place
  (developers delete their local database); from then on every change is a new revision. A
  test asserts that the migrated schema equals the models (Alembic `compare_metadata`).
- **Database**: WAL mode, `synchronous=FULL` (a commit survives a power loss), foreign keys
  enforced, the driver's legacy transaction control stated explicitly with its implicit BEGIN
  turned off, and every transaction started with `BEGIN IMMEDIATE`, so threads queue on
  SQLite's busy timeout (30 s) instead of failing on a read-to-write upgrade; a transaction
  still locked out after it gets `state_busy` (re-send). One session (one transaction) per unit
  of work (`LocalState.session`); handlers keep transactions short.
- **Opening**: the dispatch table marks which requests need the database; `Backend.execute`
  opens it before such a request runs, the first time one arrives. Opening takes the writer
  lock, then runs the migrations (`render_as_batch=True`, foreign keys off during migration) in
  one transaction, which ends with `PRAGMA foreign_key_check`: a violation rolls the migration
  back (`state_unavailable`, details in the log). The Alembic environment (`env.py`, `script.py.mako`, `versions/`) is package
  data found through `importlib.resources`; there is no `alembic.ini` (the config is built in
  code). A database carrying a revision this app does not know is refused unchanged with
  `database_too_new`. A failed opening holds nothing; the next request tries again.
- **Writer lock** (cross-process): an OS lock on `state.lock` (`fcntl.flock` on POSIX,
  `msvcrt.locking` on Windows), never waited for; the OS releases it when the process ends.
  Another process (or another backend in the same process) gets `app_busy` for any database
  request, while requests that need no database (app info, settings, credentials, scans,
  cloud setup) are served, so the CLI works while the GUI is open.
- **Batch locks** (in-process, one per batch): every request that changes a batch takes its
  lock; it is never waited for: a held lock gives `batch_busy`.
- **Runs lock** (in-process, one): taken by the requests that launch, follow or clean up runs
  and by the manifest steps; waited for at most `RUNS_LOCK_WAIT_S` (30 s, checking the cancel
  token), then `runs_busy` (re-sending resumes).
- **Lock order** is runs → batch → database transaction (innermost). A request holding batch
  locks takes the runs lock only through the bounded wait, so no two requests can wait on each
  other. Never wait for a lock or do network I/O inside `state.session()`: asking for a batch or
  the runs lock while the thread has a session open raises `LockOrderError` (a programming
  error, so `internal_error`).
- **Phase changes** are compare-and-set (`compare_and_set_phase`: one `UPDATE … WHERE phase =
  :expected` plus optional conditions such as `manifest_held`): no row changed gives
  `phase_conflict` (or `batch_not_found`), and the error rolls back the request's whole
  transaction, so a request that lost a race changes nothing. A batch row the session already
  loaded is expired after the change, so it reads the new phase.
- **Settings** (`get_settings`, `update_settings`; no database): enabled languages (default
  `DEFAULT_LANGUAGES`, at least one, validated against `WHISPER_LANGUAGES`, no duplicates),
  video and audio extensions (lower case, no dot, disjoint), include hidden entries, the
  ffmpeg/ffprobe path override, and placement defaults (strategy, output folder, folder count,
  overwrite). `update_settings` replaces the whole document: every field is required, so a
  partial document is refused with `invalid_request` instead of resetting the missing fields.
  Both responses list every Whisper language with its switch and whether WhisperX has an
  alignment model for it. The file is written atomically (temp file in the same folder, fsync,
  `os.replace`, on Windows retried for about 2 s while another process has the file open, then
  an fsync of the folder on POSIX); temp files a crashed write left (older than a minute) are
  removed; the last writer wins. Reading starts from the defaults and takes the fields the app
  knows: a missing file means the defaults, fields of a newer app are ignored and fields an
  older file lacks take their defaults; saving from an older app therefore drops the fields it
  does not know. An unreadable or invalid file gives `settings_invalid` until settings are
  saved again; a failed write gives `settings_unwritable`. Request handling never reads the
  settings: machine-dependent inputs (scan options, placement options, test mode) are request
  fields that the GUI fills from them, and the composition root (`api/wiring.py`) reads the
  adapter configuration (ffmpeg/ffprobe override) when it builds the adapters.
- **Logging**: a frontend wraps its run in `log_to_file(<its log file>)`: a human-readable
  rotating file (1 MiB, 3 backups, UTF-8) in the user log dir, the app's loggers from INFO,
  other libraries from WARNING, warnings captured. One file per frontend keeps the GUI and the
  CLI from rotating one file; two runs of one frontend may still share it, which the handler
  (`SharedRotatingFileHandler`) tolerates: rotation renames the file first and shifts the
  backups only after that worked; when the rename fails (Windows, another process has the file
  open) it keeps appending and retries after another 64 KiB; before each record it reopens the
  path when another process rotated the open file away (POSIX). Nothing reaches stderr: a
  record that cannot be written at all is dropped. The log dir is created with the first
  record. An unexpected exception's traceback goes only there (§3.1).

## 4. R2 object layout (contract)

```
batch_metadata/<batch_id>.json            manifest, written by desktop only, last (= ready)
<batch_id>/audio/<ulid>[.a<track>].<ext>  input audio, written by desktop only
<batch_id>/results/<ulid>.<lang>.srt      subtitle, written by worker only (empty if no speech/failed)
<batch_id>/results/<ulid>.<lang>.json     job result (= done marker): status ok|no_speech|failed, error, timings,
                                          engine settings, aligned
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
`windows|posix`), `items[]`. Item: `item_id` (ULID), `original_path` (absolute), `source_size`,
`source_mtime_ns`, `media_kind`, `audio[]` (`object_key`, `track_index` (null for an audio
file), `extension`, `codec`, `duration_s`, `size_bytes`), `jobs[]` (`language`, `mode`,
`audio_object_key`).

Implementation: `packages/contracts` (`classes.py` models, `layout.py` key builders/parsers,
`reading.py`). Readers always go through `reading.read_*(key, body)`: it refuses another major
version before validation (`UnsupportedSchemaError`) and checks that the object's batch / item /
language / run id matches its key. Stored objects accept any well-formed language code (forward
compatibility); the Whisper language set is enforced where the desktop app builds requests.

Paths are stored as text exactly as the OS reported them (no case or Unicode normalisation),
plus `path_flavor`. Names that are not valid Unicode (unpaired surrogates on Windows, non-UTF-8
bytes on POSIX) are rejected at scan time with a visible warning instead of being mangled.
How names are compared (sidecar stems, selection paths) is the path-flavour rule of §3.2.

## 5. Worker (Runpod)

### 5.1 Image

- Base: `nvidia/cuda:12.8.1-base-ubuntu24.04` + uv-managed Python 3.13 + the worker's
  locked dependencies (WhisperX 3.8.x, `torch~=2.8` cu128 from the PyTorch index) + system
  ffmpeg. cuBLAS/cuDNN 9 come from the torch wheels; `LD_LIBRARY_PATH` points CTranslate2 at
  them, and start-up asserts that CTranslate2 sees the GPU. No sshd (proxied basic SSH).
  The Runpod `cu1300`/`cu1320` images are out (CTranslate2 has no CUDA 13 wheels); the
  `cu1281-torch280` image fits but keeps Python/torch outside `uv.lock`.
- Baked models: `large-v3` (CT2 fp16, 3.09 GB), the alignment models for `hu` (HF) and
  `en` (a torchaudio bundle, which lives in the torch hub cache, not the HF cache). `HF_HOME`
  and `TORCH_HOME` point to the same fixed paths at build time and run time. WhisperX 3.8
  defaults to pyannote VAD; the worker uses Silero. WhisperX's own Silero loader calls
  `torch.hub.load("snakers4/silero-vad")`, which contacts GitHub on every start, so
  `engine/silero.py` builds WhisperX's own `Silero` while `torch.hub.load` is temporarily
  redirected to `$TORCH_HOME/hub/snakers4_silero-vad_master` (`source="local"`), and passes
  it as `vad_model`. The image bakes that repository from a pinned release commit (v6.3); the
  archive is downloaded with retries, its SHA-256 checked and it is unpacked safely
  (`engine/archive.py`). Careful mode uses faster-whisper's Silero ONNX model, which ships with
  the package. The Whisper model is loaded with `local_files_only`, so the engine never
  contacts Hugging Face for it. Alignment models for other enabled languages are downloaded at
  run time to the container disk; if that fails, the job runs unaligned.
- The `engine` extra pins `torch~=2.8.0` (WhisperX's pin) from the PyTorch cu128 index with a
  `sys_platform == 'linux'` marker, so the universal `uv.lock` still resolves on Windows/macOS.
- `apps/worker/Dockerfile` (built from the repository root; `.dockerignore` keeps tests, docs
  and secrets out of the context): uv (pinned by digest) installs Python 3.13 and the locked
  environment into `/opt/venv` (`--frozen --no-dev --package transcribe-worker --extra engine`,
  dependencies before the workspace code so they are cached), `LD_LIBRARY_PATH` points at the
  cuBLAS/cuDNN libraries of the torch wheels, `python -m transcribe_worker.engine.bake`
  downloads the models into `HF_HOME=/opt/models/huggingface` and `TORCH_HOME=/opt/models/torch`,
  and the entry runs as `tini -- transcribe-worker-entry`. Both base images are pinned by digest.
- The CI image build frees runner disk space first (the image is roughly 10 GB).
- Published to public Docker Hub as `markfazekasgh/transcribe-ui-worker` by CI with tags
  `sha-<short>`, `latest` and `schema-<contracts major>`. The desktop app uses the
  `schema-<major>` tag of the contracts version it was built with, so it always gets a
  compatible worker even when a desktop-only change did not rebuild the image.

### 5.2 Process model

```
entry (PID 1 via tini, stdlib-only Python module, no app imports)
 ├─ 1. spawns the watchdog: a separate stdlib-only Python process that sleeps until
 │     MAX_RUNTIME_S (parsed with a safe default if invalid), then terminates the pod
 ├─ 2. runs `transcribe_worker.run` as a child process (imports, config, GPU check, pipeline)
 └─ 3. after the child exits for any reason: terminate own pod; with KEEP_POD_ALIVE=true
        (test mode) it sleeps instead, and the watchdog ends the pod after MAX_RUNTIME_S
MAX_RUNTIME_S: 2 h in test mode; in production derived from the total audio duration
            with a margin, passed by the desktop app at launch
terminate = DELETE /v2/pods/$RUNPOD_POD_ID, retries with backoff; after a few failures also
            POST /v2/pods/{id}/action {"action":"stop"}; verified by GET. It never gives up:
            the entry and the watchdog keep retrying and never exit while the pod exists
```

The entry never exits on its own: an exiting PID 1 presumably makes Runpod restart the
container [I, to verify at the first approved run], which would start a fresh, billed worker and
reset the watchdog. Further rules (implemented in `apps/worker/.../entry/`, covered by flow
tests):
- The entry keeps its own deadline: the child is killed at `MAX_RUNTIME_S`, and in test mode
  the entry terminates the pod itself at the deadline or as soon as the watchdog has died.
- SIGTERM/SIGINT/SIGHUP end the run phase through `finally`; from the termination phase on
  they are ignored, so a repeated SIGTERM cannot interrupt it.
- Any error during a terminate attempt is retried, never propagated.
- A 404 counts as "gone" only after the pod was seen (start-up probe, successful DELETE):
  with a wrong pod id or base URL every call returns 404. An unknown pod does not start the
  worker; a pod id without an API key does not start it either and waits to be killed from
  outside (layers 3-5) instead of exiting.
- The client sends the key only to `https://*.runpod.io` (or `http://127.0.0.1` in tests),
  refuses redirects, and error messages never include response or request data.

Termination layers, each sufficient on its own for the failures it covers:

1. Entry `finally` after the child exits (normal end, crash, config error, missing GPU).
2. Watchdog process, independent of the entry and the child: covers hangs and a blocked entry.
3. Desktop app deadline and startup reconciliation (only while the app runs, §3.7).
4. External reaper (`transcribe_worker.reaper`, CLI `transcribe-worker-reaper`, run by
   `reaper.yml` every 15 minutes with a pods-only key): one pass lists `GET /v2/queued-pods`
   and `GET /v2/pods` (all pages), cancels queued pods in status `QUEUED` and terminates pods
   not yet `TERMINATED` whose name parses and whose deadline plus a 10-minute grace (so it does
   not race layers 1-2) has passed, then confirms each by `GET` (404, `TERMINATED`, or a queued
   pod no longer `QUEUED`). Queued pods go first, so one that deploys while being cancelled
   shows up in the pod list read afterwards. A name with the prefix that does not parse is left
   alone and fails the run; other pods are never touched or logged by name. List items are
   parsed leniently: any JSON value (or none) of `id`, `name`, `status` and `createdAt` is
   accepted, a non-string becoming null, so one odd entry or format change cannot fail the
   list; list items that are not JSON objects are dropped unlogged and fail the run. Entries
   without a string name are foreign (never logged); an app entry whose status is not a string
   fails the run but is still handled by its deadline; app entries are validated one by one. An
   app entry whose id is not a string fully matching `[A-Za-z0-9_-]{1,64}` is left alone and
   fails the run. A deadline not yet due that lies more than 32 days (`MAX_DEADLINE_SPAN`:
   Runpod's 30-day queue maximum + the 24 h `MAX_RUNTIME_S` ceiling + a day of margins) after
   the entry's `createdAt` is treated the same way, so a wrong deadline cannot silently disable
   this layer; deadline arithmetic cannot overflow (year 9999 included). A missing, non-string,
   unparseable or naive `createdAt` fails the run, but the entry is still handled with the pass
   start as its creation time (a `createdAt` after the pass start is capped to it). Untrusted
   names and ids are logged truncated and escaped (`%r`), so they cannot inject workflow
   commands. Each call is retried a few times within the pass. The queued phase starts no new
   cancel after 1 minute from the pass start, its list included (a slow list fails the run, and
   a queued pod that deploys meanwhile is caught by the pod phase); the pod phase always lists
   and starts terminates until 4 minutes after the pass start or 2 minutes after its own start,
   whichever is later, so a slow queued phase cannot starve it. The hard bound is the job's
   15-minute timeout. Assuming one-page lists and that the 30 s HTTP (socket) timeout bounds
   each operation (a trickling response or slow DNS lookup can exceed it), the pass ends after
   about 10.7 minutes (one action up to 231 s; the arithmetic is in `reaper/constants.py`). Any
   failure exits 1 while the other pods are still handled. `--dry-run` sends no DELETE. Covers
   failures before Python starts (image pull failure, crash loop) and queued pods that start
   while the desktop app is closed. The HTTP layer is the entry's `ApiSession` (same URL,
   redirect and error-message rules).
5. Account spend limit and low-balance alert (a zero balance stops pods instead of
   terminating them, which still bills volume storage).

The entry module is deliberately tiny and stdlib-only, so an import error in the app cannot
prevent termination; a CI test runs it inside the built image against a fake API. Whether the
pod-scoped `RUNPOD_API_KEY` may delete its own pod is checked at the first approved run (§7);
if not, a separate
pods-only key is passed as a Runpod secret.

### 5.3 Pipeline

- Discover: list `batch_metadata/`, load manifests, skip batches with `_done.json`.
- Order: all pending jobs of the run are sorted by language, then batch, then item, so the
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
- Per job: ffmpeg decodes the first audio stream to 16 kHz mono 16-bit WAV (robust for
  AC3/E-AC3/DTS/TrueHD) → WhisperX `large-v3`, fp16, forced language, batch 16, Silero VAD →
  wav2vec2 alignment (skipped with `aligned: false` when the language has no model) → quality
  checks on the raw transcript (empty output; zlib compression ratio > 2.4 in any 500-character
  window, since whole long texts of normal speech reach 2.4–3.0; the same segment repeated; a
  repeated n-gram) → on suspicion, a re-run in careful mode (a `careful` job uses careful mode
  from the start; if the re-run fails, the first transcript is kept) → empty segments dropped
  and repetition loops (3 or more identical consecutive segments, case and punctuation
  ignored) collapsed to their first segment; a sentence said twice stays → cue builder (max
  42 chars/line, 2 lines, 1–7 s, split at sentence ends, pauses > 0.6 s and clause marks; a
  dot after an initial, a dotted acronym or a one- or two-digit ordinal is no sentence end) → SRT.
  No cues means `no_speech` with an empty SRT.
- Cue timing: a cue lasts at least 0.1 s even when engine word times overlap; the later cues
  move for it, but at most 0.5 s: beyond that, the pile of cues shares the time evenly, so
  overlapping times cannot shift the rest of the file.
- Unspaced scripts (Chinese, Japanese, Thai, Lao, Khmer, Myanmar, Tibetan) are detected by
  character (Unicode block), not by `Transcript.language`, because Whisper output mixes in
  Latin words. In a segment containing unspaced text, words are joined without a space where
  the segment text has none between them (WhisperX aligns these languages per character),
  or, for words not found in the segment text, next to an unspaced character; a segment
  following unspaced punctuation ("。") is joined too. Spaces the engine wrote (Thai phrase
  breaks) stay; spaced text is never joined.
  Unspaced text is split into units (a character with its combining marks and attached
  punctuation; no break before closing marks or small kana, nor after opening marks) that
  share the word's time, or the segment's when untimed, in proportion to length, so the
  line and cue limits hold; lines and cues may break between units. Thai and similar
  scripts therefore break mid-word (no dictionary); CJK lines count characters, not display
  width (open product question Q22); the n-gram quality check splits on spaces, so it does not see loops inside unspaced
  text (the compression and segment checks still do).
- The engine runs in its own process (`engine/host.py`, spawned, models loaded once), because
  a CUDA call cannot be interrupted: the per-job timeout (at least 10 min, 1 s per second of
  audio, doubled in careful mode) kills that process, the job is `failed`, and the next job
  starts a fresh engine. An engine crash (native abort, OOM kill, even while idle) is handled
  the same way. A decode error, a missing audio object or any engine or subtitle error marks
  only that job `failed` (the error text goes into the result) and the run continues. A
  storage or disk problem while preparing the audio is not the file's fault: the job gets no
  result and the next run tries it again. If the engine cannot be started (at the beginning or
  after a crash), the run fails (exit 1) without writing results for the remaining jobs.
- Order inside a run: batches whose results are all in R2 get their `_done.json`, then results
  from the backup are uploaded, and only then does the engine start, so a broken engine never
  keeps backed-up results from reaching R2.
- Alignment: the language's aligner is loaded before decoding; if it cannot be loaded
  (download, disk or memory problem), the job is transcribed without word alignment
  (`aligned: false`) instead of failing, and the next job of that language tries again. If
  aligned parts lose any letter of their segment (WhisperX drops parts whose times stay NaN),
  the segment is kept whole and unaligned.
- Careful decoding uses faster-whisper's Silero VAD with 500 ms minimum silence and 400 ms
  speech padding (its default 2 s silence lets music between lines through).
- Careful decoding is faster-whisper's sequential `WhisperModel.transcribe` on the same
  CTranslate2 model (batch size 1, beam 5, temperatures 0–1.0 in 0.2 steps with the
  compression-ratio/log-prob/no-speech checks, `condition_on_previous_text=False`, its Silero
  VAD): WhisperX's batched pipeline decodes each chunk once (with its default `beam_size` 5)
  and ignores temperatures and fallback thresholds, so `asr_options` cannot express it.
  Alignment runs one segment at a time; a segment WhisperX returns nothing for (its times stay
  NaN) keeps its transcription times without words. Only
  the engine adapter modules (`engine/whisperx_engine.py`, `engine/silero.py`,
  `engine/bake.py`) import the GPU stack; the conversion of WhisperX
  output into the engine `Transcript` (`engine/convert.py`) is pure and tested in CI.
- Backup: each result file is also written to the Global Volume
  (`/workspace/backups/<R2 key>`) as a whole file (object-store semantics: no rename-based
  atomicity there): the SRT first, then the result JSON, which is never overwritten and marks
  a complete backup. When planning, a job whose result is missing in R2 but has a valid backup
  (SRT and JSON) is uploaded from the backup instead of transcribed. Backup errors are only
  logged; R2 is the primary copy.
- Upload: the SRT is not written when the job's JSON already exists (another worker finished
  it), then the JSON with `If-None-Match: *`. A failed upload leaves the batch open; the next
  run uploads the backup. Two overlapping workers could still pair one worker's SRT with the
  other's JSON (the SRT PUT is unconditional); the single-worker rule (§3.7) keeps that out.
- Timeouts keep a stuck call from holding a billed pod: R2 requests have 10 s connect and 30 s
  read timeouts with 5 attempts; at the end of a run the heartbeat and prefetch threads, and an
  in-flight status upload, are waited for at most 60 s each (the final status is then written
  over the stuck upload).
- Anything left for the next run (audio not prepared because of storage, disk or ffmpeg
  problems, including a disk filling up during decoding; a result or backup that could not be
  uploaded; a batch summary that could not be written) makes the run end as failed (exit 1)
  once every other job is done, so the app never reports a clean run with work missing.
- A batch whose jobs all have a result in R2 gets `_done.json` with the counts of every result
  (earlier runs included), also when an earlier run stopped right before writing it. After the
  last job (and nothing left for the next run): exit 0, and the entry terminates the pod.
- Run status: `status.json` and the JSON-lines log are uploaded at start, on every job change
  and every 30 s (`starting` → `transcribing` → `finished`, or `failed` with the error when the
  run itself fails, e.g. R2 unreachable).
- Configuration (environment; values are never logged, and a validation error names only the
  field): `TRANSCRIBE_RUN_ID` (ULID from the desktop app; generated when missing),
  `R2_ENDPOINT_URL`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and optional
  `TRANSCRIBE_BACKUP_DIR` (default `/workspace/backups`), `TRANSCRIBE_WORK_DIR` (default
  `/tmp/transcribe-worker`: audio cache, decoded audio, log), `TRANSCRIBE_AUDIO_CACHE_GB`
  (default 20), `TRANSCRIBE_ENGINE` (`whisperx`, or `fake` for the image smoke test), plus the
  entry's `RUNPOD_POD_ID` and `MAX_RUNTIME_S` (the status deadline).

### 5.4 Credentials in the pod

An R2 API token scoped to the single bucket (R2 cannot scope by prefix) is stored as Runpod
Secrets and referenced from a Runpod template that the desktop app creates/updates via API;
the image and the pod-create request never contain credentials.

## 6. Cross-cutting

- Typing: mypy strict with the pydantic plugin; Pydantic v2 models are frozen where possible.
- Errors: domain exceptions per module; frontends map them to error responses
  (`{"error": {"code": ..., "message": ..., "details": ...}}`), never stack traces.
- Logging: stdlib `logging`; JSON lines in the worker (uploaded per run), a human-readable
  rotating file in the user log dir in the desktop app (§3.8). Secrets are never logged; the worker
  never logs its environment (it contains `RUNPOD_API_KEY` and the R2 secrets).
- R2 client: boto3 with `request_checksum_calculation="when_required"` and
  `response_checksum_validation="when_required"` (botocore ≥ 1.36 default checksums are not
  reliably supported by R2), region `auto`, multipart ≥ 64 MiB parts.

## 7. Testing strategy

Few unit tests; mostly regression and flow tests:

- **Contract tests**: JSON Schema snapshots of the stored objects (manifest, results, run
  status, batch summary) in `packages/contracts/tests/snapshots/`, and of the desktop's
  `Request`/`Response` unions in `apps/desktop/tests/snapshots/`; any change is a visible diff.
- **Desktop flow tests**: a tiny media tree is generated at test time with ffmpeg `lavfi`
  sources (multi-track video, AAC/Opus/AC3/PCM audio, existing sidecar subtitles); S3 is
  `moto`; Runpod is an `httpx` mock. Flows: scan → filter → select → create batch → convert +
  upload (interrupted and resumed) → simulated worker results → every placement strategy →
  cleanup. Each flow runs the same request JSON through the CLI entry point and through the
  backend directly and asserts identical responses (golden files). Golden flows use a fake
  media adapter and normalise temp roots, path separators and the path flavour, so the files
  match on every OS; flows with the real ffmpeg assert structure, since its output differs
  across builds.
  - Parity runner (`apps/desktop/tests/support/parity.py`): each leg gets a fresh temp root and
    a backend from the same seeded factory (fixed clock, seeded ULID randomness); the backend
    leg calls `Backend.execute_json`, the CLI leg calls the CLI's `main(["run", <file>])`
    in-process with the same backend injected. Responses, progress events and exit codes are
    compared step by step after normalisation. A request document may contain `<root>`, which
    each leg replaces with its own root; an optional `prepare` function fills each leg's root
    first (a media tree of empty files with fixed mtimes, `support/trees.py`). One test runs the installed `transcribe-ui` entry
    point as a subprocess.
  - Normalisation touches only `LocalPath` and `PathFlavor` fields (their serializers apply
    rewrite functions passed in the serialization context): the leg's root, as created and as
    resolved (`os.path.realpath`; symlinks, Windows 8.3 short names such as `RUNNER~1`) →
    `<root>`, separators → `/`; the flavour → `<flavor>`. Test trees get fixed mtimes, and the
    clock, time zone and ULID randomness are fixed, so the normalised JSON is byte-identical on
    every OS.
  - Golden files live in `apps/desktop/tests/golden/` and are compared byte for byte;
    `UPDATE_SNAPSHOTS=1` regenerates them and the schema snapshots. On a rebase conflict in a
    golden or snapshot file, regenerate it; never merge it by hand.
- **Worker flow tests**: fake transcriber behind the engine protocol + `moto` (the WhisperX
  stack is an optional extra, not installed in CI): discovery, language ordering,
  prefetch pipeline, resume after a kill, failure isolation (one failing file, one empty
  file), `_done.json`, and the supervisor always calling terminate (child crash, hang →
  watchdog, normal exit; `KEEP_POD_ALIVE`).
- **Reaper flow tests**: a full pass against a fake Runpod API on 127.0.0.1 (pagination,
  overdue / in-grace / in-time / foreign / malformed names, queued pods, a DELETE that fails
  or does not take effect, dry run, pass budget, the command line).
- **GPU smoke test** (marker `gpu`, only with the architect present): real WhisperX on a
  short Hungarian and English clip on the pod, started from an interactive SSH session (the
  proxied SSH has no SCP/SFTP; `POST /v2/pods/{id}/exec` is the non-interactive alternative).
- **First approved Runpod run** (only with the architect present, one check at a time)
  verifies what the docs leave open: the pod-scoped key may delete its own pod; `queue`
  works on the account together with a Global Volume; `{{ RUNPOD_SECRET_x }}` resolves in the
  pod's env; the CUDA 12.8 image runs on a host whose driver reports CUDA 13.x; proxied
  basic SSH is available; an exiting PID 1 restarts the container.
- Markers (read-only Runpod calls excepted from the architect rule, see `tooling.md`):
  `integration` (real R2/Runpod) and `gpu` (needs the GPU stack); both are deselected
  by default and in CI.
- **GUI**: pytest-qt smoke tests and view-model → request mapping tests.

## 8. CI/CD (summary; details in `tooling.md`)

- `ci.yml` runs on every pull request and push to `main` (no workflow-level path filter, so
  required checks always report). A first job detects which members changed; lint, typecheck
  and test jobs per member run only when needed (the desktop member on Ubuntu and on
  `windows-2025`); one aggregate `ci-ok` job is the single
  required status check. gitlint checks PR commits.
- `release-desktop.yml` on push to `main` when desktop/contracts code changes: PyInstaller
  one-file builds on Windows, macOS arm64, macOS x86_64 and Ubuntu 22.04, smoke-run
  `--version`, upload only the executable (`actions/upload-artifact` with `archive: false`)
  and attach the four executables to a GitHub Release. Builds are unsigned for now (macOS
  users clear the quarantine flag; Windows SmartScreen may warn).
- `release-worker.yml` on pull requests and pushes to `main` when worker/contracts code
  changes: build the Docker image and smoke-test it (a full pod run with the fake engine, a fake
  Runpod API and moto S3; then, inside the image, the CUDA libraries load and, with no
  network, the Whisper model, the baked hu/en aligners and the Silero VAD load the way the
  engine loads them). Only on `main` a
  separate job in the `dockerhub` environment (deployment branch policy: `main` only, so no
  other branch gets the token) loads the image from an artifact and pushes it; it never runs
  the image.
- `reaper.yml` every 15 minutes and on manual dispatch (with a dry-run input): runs the
  reaper (§5.2, layer 4) with `RUNPOD_REAPER_API_KEY` from the `runpod-reaper` environment,
  restricted to `main` the same way. It installs only the worker package without the `engine`
  extra, never overlaps itself (concurrency group) and times out after 12 minutes. While the
  secret is missing or empty the run only emits a `::warning::` and succeeds, so it does not
  fail every 15 minutes before the key exists; with the key, any failure or a malformed
  `transcribe-ui-*` name fails the run, and GitHub notifies. GitHub disables scheduled
  workflows of a public repository after 60 days without repository activity: the reaper
  then stops silently until it is re-enabled in the Actions tab (layer 5 still applies).
- Nothing is published from a pull request.

## 9. Ideas for later (not planned)

- **Spoken-language detection.** Today the user always chooses the language. Options if it is
  wanted later: the language tags of video soundtracks (free, often missing or wrong; the
  review step already shows them); Whisper detection on the cloud machine (estimated about a
  second per file, from several 30 s speech windows so a music intro does not decide),
  reporting a mismatch with a Retry in another language; a small local model before upload
  (estimated a few seconds per file and about 100 MB larger download).
- **Presigned R2 URLs** in a per-run job object instead of a bucket-scoped key, so the pod
  holds no long-lived R2 credential.

## 10. Delivery plan (milestones)

Each milestone ends in a mergeable, verified state. The PR that completes a milestone (or a
part of one) marks it **(built)** and updates `docs/ai/status.md`. A milestone that needs more
than a few PRs is planned part by part in `docs/ai/plans/mN.md` before its first PR. The current position and the
next step are in `docs/ai/status.md`.

- **M1** (built): Foundation: repo, uv workspace, lint/type/test tooling, CI, `contracts`
  (§2, §4, §8).
- **M2** (built): Worker: pipeline, subtitles, entry/watchdog self-termination, image with
  smoke test and Docker Hub release (§5).
- **M3** (built): Cloud safety net: the external reaper (§5.2 layer 4, §8).
- **M4**: First approved Runpod run, worker only, with the architect present: the checks of
  §7 "First approved Runpod run" (whether the pod-scoped key deletes its own pod decides if a
  separate pods-only key is passed, §5.2; PID 1 restart behaviour; these include items 1-4 of
  `research/runpod.md` "Items to test with a real key").
- **M5** (planned in `plans/m5.md`; M5.1-M5.4 built): Desktop backend: façade, discovery and
  selection, batches, conversion and resumable R2 upload, credentials, worker launch and
  monitoring, download and placement (§3.1-3.7).
- **M6**: Desktop frontends: GUI and CLI over the same requests (§3.1).
- **M7**: End-to-end run from the desktop app, with the architect present.
- **M8**: Desktop release: `release-desktop.yml` one-file builds for the four platforms (§8).
- **Later**: MCP frontend over the same requests.
