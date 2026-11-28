# Architecture

Status: **approved design**. Requirements and human decisions: `docs/human/requirements.md`.
Open questions: `questions-for-architect.md`, `questions-for-product.md` (open items here say
so). Research behind the decisions: `docs/ai/research/`.

## 1. System overview

```
 Desktop app (Windows / macOS / Linux, GUI + CLI executables)        Cloudflare R2 (S3 API)
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

- Two entry points (executables per platform, §8): the GUI executable `transcribe-ui-gui`
  (`[project.gui-scripts]`, `transcribe_ui.gui.main:run`; windowed, contains Qt, opens the GUI)
  and the CLI executable `transcribe-ui` (`[project.scripts]`, `transcribe_ui.cli.main:run`;
  console, no Qt: `run`, `schema`, `--version`, `credentials`; no `gui` command, and a bare
  call keeps answering `invalid_arguments`). Both call the same façade. The CLI entry and
  everything it imports must never import Qt (`PySide6`) or `transcribe_ui.gui`: a test imports
  the CLI module, the routes and every handler module in a subprocess, runs `schema`,
  `--version` and a request that needs no database, then checks `sys.modules`.
- The backend exposes one façade: `Backend.execute(request: Request, progress: ProgressSink,
  cancel: CancelToken) -> Response` (`execute_json` validates a JSON document first; an invalid
  one is answered with the error `invalid_request`). `Request` is a Pydantic discriminated union
  on `action` (for example `get_app_info`, `scan_tree`, `preview_selection`, `create_batch`,
  `review_batch`, `run_upload`, `launch_worker`, `refresh_status`, `plan_download`,
  `run_download`, `cleanup_cloud`, `export_custom_plan`, `import_custom_plan`). `Response` is a
  union discriminated on `status`:
  - `ok`: the request's own response model (discriminated on `action`);
  - `needs_confirmation`: nothing was changed; it lists the request fields to set, each with
    the value that confirms it (`set_to`: `true` for a flag, the plan's fingerprint for
    `confirm_fingerprint`), so every frontend confirms by re-sending the same request with each
    `field` set to its `set_to` (confirmations are data, so GUI, CLI and MCP behave the same).
    Every request that deletes or replaces something the user made asks this way, including
    `delete_batch` and `exclude_items` (`confirm`), and so does stopping a run (`cancel_worker`,
    `confirm`; Q34);
  - `error`: `{"status": "error", "action": ..., "error": {"code", "message", "details"}}`
    (§6). Handlers raise `DomainError(code, ...)`; an unexpected exception becomes
    `internal_error` with a generic message (the traceback goes to the log only).
- Models live in the desktop app (not in contracts: they are not shared with the worker): the
  unions in `transcribe_ui/api/classes.py`, each sub-package's request and response models in
  its own `classes.py`, built on `api/base.py` (`FrozenModel`, `LocalPath`, `PathFlavor`,
  `SetPath`, `ToolPath`, `WhisperLanguage`, `check_distinct`), so
  `api/classes.py` can import them without a cycle. A path on the user's computer is typed
  `LocalPath`, the host's path flavour `PathFlavor`; golden-file tests normalise exactly these
  fields (§7).
- The dispatch table (`api/routes.py`) maps each action to its handler and marks whether it
  needs the database; request types that do not (app info, settings, credentials, scans, cloud
  setup; their handlers are listed in `api/local_handlers.py`) never open it, so the CLI can run
  them while the GUI holds the writer lock.
- Cancellation: long requests check the `CancelToken` between items and stop with the error
  `cancelled` after persisting what is done; re-sending the request resumes.
- Frontends only build requests and render responses/progress events:
  - **CLI** (argparse): `transcribe-ui run request.json` validates the file into `Request`,
    prints the `Response` as indented JSON; progress events go to stderr as NDJSON; both UTF-8
    with `\n` line ends on every OS. Nothing else reaches stderr (apart from the guidance of
    `credentials`, below; log records and warnings go to
    the log file, §3.8), and stdout gets a JSON response whatever fails: an
    unexpected exception outside the backend gives `internal_error` (exit 1), a Ctrl+C before a
    request was read gives `cancelled` (exit 130). The one exception is `--help`/`-h`: argparse's
    usage text, exit 0. The request file must be UTF-8; a UTF-8 byte order mark is stripped,
    and a UTF-16 file (PowerShell 5.1 `>` / `Out-File`) is refused with `invalid_request`
    ("must be UTF-8"). Ctrl+C sets the cancel token, a second Ctrl+C stops at
    once (every step is idempotent, so the state is consistent either way). Exit codes: 0
    success, 1 error response, 2 request file (or command line) invalid, 3
    `needs_confirmation` (so `delete_batch` and `exclude_items` without `confirm` exit 3 since
    M6.1, and `cancel_worker` without `confirm` since M6.6: a script that stops a run must now
    send `"confirm": true`), 130 `cancelled`. `transcribe-ui schema` prints the JSON Schema of
    `Request` (`--response`: of `Response`) so requests can be written by hand or by an LLM;
    `--version` prints the app version.
  - **CLI ergonomics** (M6.7; no new semantics, `run` stays the way to every request):
    - `transcribe-ui run -` reads the request from stdin up to its end, with the same rules as
      a file (UTF-8, BOM stripped, UTF-16 refused) and the same response and exit code, so a
      script or an LLM can pipe it (a file named `-` is given as `./-`). Windows PowerShell
      5.1 pipes text to a native program in `$OutputEncoding`, ASCII by default, which turns
      every non-ASCII character (an accented path) into `?` before the app sees it; there,
      run `$OutputEncoding = [System.Text.UTF8Encoding]::new($false)` first, or use the file
      form, which is recommended on 5.1. PowerShell 7 pipes UTF-8.
    - `run --progress text` writes one readable line per progress event to stderr instead of
      NDJSON: `<stage> <completed>[/<total>][: <path>]` in the language of the texts (stage
      from the catalog, numbers in the language's format; a path's characters that are not
      printable, such as a line break or a terminal escape, shown escaped as Python writes
      them). The default stays `ndjson`.
    - `transcribe-ui credentials` asks for each credential in turn (R2 endpoint URL, bucket,
      access key ID, secret access key, Runpod API key), so no value sits in a file or in the
      shell history: the endpoint and the bucket with echo (read from stdin, the label on
      stderr), the keys and the secret without (`getpass`); Enter alone skips a field (the
      stored value stays). Each answer is checked at once by the request's own validation
      (a one-field `store_credentials` document); a refused one shows the field's rule (never
      the value) and is asked again, so a typo does not lose the answers after it. Guidance
      goes to stderr, nothing to stdout but the response. The answers form the
      `store_credentials` request a file would hold, so the response and exit code equal
      `run` with that file; nothing given answers `invalid_request` like an empty request.
      Refused with `invalid_arguments` (exit 2) when stdin is not a terminal (getpass would
      then read the pipe, with echo): use a request file there. Ctrl+C, or the end of input
      (Ctrl+D; Ctrl+Z on Windows, where `getpass` returns it as a character) while asking
      stores nothing (`cancelled`, 130). The prompt is an injected port (`cli/prompt.py`),
      scripted in tests.
    - Confirming: a request that needs a confirmation exits 3 with a `needs_confirmation`
      response listing each `field` and its `set_to`; add those fields with those values to
      the request file (or the piped document) and run it again. For example
      `{"action": "delete_batch", "batch_id": "...", "confirm": true}` (stopping a run likewise:
      `{"action": "cancel_worker", "run_id": "...", "confirm": true}`); a plan confirmation
      sets `confirm_fingerprint` to the fingerprint the response gave. Nothing changed
      before the second run.
  - **GUI** (PySide6, `transcribe_ui/gui/`): view-models translate widget state into the same
    request models; see "GUI" below.
  - **MCP (later)**: one FastMCP tool per request type; the tool signature is the request
    model, so no new business logic is needed.
- Determinism: clock, local time zone, ULID generator, filesystem root and external clients
  (R2, Runpod, ffmpeg) are injected ports. Given the same request and the same port fakes, the backend
  returns byte-identical JSON regardless of frontend. Flow tests assert this directly
  (section 7).
- Requests are validated only against machine-independent rules (for example the full Whisper
  language set, not the locally enabled languages), so the same request JSON is accepted or
  rejected identically everywhere. Local settings only shape what the GUI offers.
- Long operations (upload, download) are resumable commands: re-sending the same request
  continues from persisted state instead of starting over.
- **Language of the texts** (Q32; built in M6.14, code `transcribe_ui/i18n/`):
  - Catalogs: GNU gettext, as Django uses them: `transcribe_ui/locale/{en,hu}/LC_MESSAGES/
    transcribe_ui.po`, the compiled `.mo` next to them and the template `transcribe_ui.pot`
    (package data of the `transcribe_ui` package; `locale` is a plain folder, not a package),
    read with the standard library's `gettext` (`i18n/catalog.py`; no Qt, so the GUI, the CLI
    and later MCP share one catalog; not Qt's `.ts`/`.qm`). The msgids are the English texts;
    `en.po` leaves every msgstr empty (the msgid is shown) and exists so wording can change
    without a code change. Code marks texts with `tr()` and `ntr()` (plural forms), not `_`,
    which the linters reserve for unused names. A frontend activates its language once at
    start-up, before it builds a text, so no text is translated at import time: constants hold
    no texts, and texts several GUI modules show are functions (`gui/texts.py`). A catalog that
    cannot be read logs a warning and shows the English texts.
  - Language choice: the setting `language` (§3.8): `system` (default), `en` or `hu`. With
    `system` the OS user interface language decides, read through an injected port
    (`i18n/system.py`, `os_language`): Windows `GetUserDefaultUILanguage` (its LANGID's locale
    name); macOS the first entry of `/usr/bin/defaults read -g AppleLanguages` (5 s limit);
    Linux and other POSIX the first set variable of `LANGUAGE`, `LC_ALL`, `LC_MESSAGES`, `LANG`,
    taking the first entry of `LANGUAGE`'s `:`-separated list. Each value is cut at `_`, `.`,
    `@` or `-` and lower-cased (`C` and `POSIX` mean English); a read that fails gives no
    language. Hungarian when that language is `hu`, otherwise English (`i18n/language.py`).
    The frontends read the setting with `Backend.language_setting()` (no request, never
    fails: `SettingsStore.language` reads only that field, so a missing, unreadable or invalid
    file, or an invalid value, counts as `system`, while a broken other field does not change
    the language). Each frontend resolves it once at start-up: `run_gui` before it builds the
    window, and it also installs Qt's own translation (`qtbase_hu.qm` from Qt's translations
    folder) for the texts Qt shows itself (standard buttons such as Cancel, context menus);
    the CLI before it builds its parser (it builds its backend first, which opens nothing; it
    has no `--lang` flag, since its JSON is not translated). A change in Settings applies
    after a restart: the GUI asks "Restart now to change the language?" (Restart now / Later;
    a restart goes through the close handling of Q30; built with the settings page, M6.3, see
    "GUI" below).
    Switching live would need every page to re-render its texts on a signal, which costs
    every GUI part extra work and tests for a rare action.
  - The backend stays language-neutral: responses, codes, `details`, progress stages and the
    stored data do not depend on the language, so the same request gives the same response
    everywhere (parity unchanged; a test compares the backend's and the CLI's bytes under both
    languages). Every text the backend returns has a stable code and its values in `details`,
    next to an English `message` (the text of the log, the CLI JSON and an LLM reading it):
    - errors: `ErrorBody.code`;
    - confirmations: `Confirmation.code` (`delete_batch`, `exclude_items`,
      `existing_languages`, `launch_plan`, `cleanup_audio`, `cleanup_everything`,
      `cleanup_not_downloaded`, `cleanup_unknown_batch`, `overwrite_targets`,
      `terminate_pod`, `cancel_queued_pod`, `cancel_worker`);
    - placement plan errors and download failures: their `code`, with `details` (`file`: the
      media file's name, `language`, and the values a message adds, such as `name`);
    - placement plan reasons (`renamed_by`), scan warnings, item problems, progress stages and
      the cost estimate's `price_source` (`fallback`): their codes;
    - `check_credentials`: `ServiceCheck.code` on every outcome (`r2_check_passed`,
      `runpod_check_passed`, else the error's code) and the error's `details`;
    - the desktop's own job failures: `JobView.result_error_code` (`result_unreadable`,
      `result_missing`; stored in the job payload, revision `result_error_code`; None for the
      worker's own errors, which are shown as they are);
    - the states the jobs pages show: batch phases, stages and labels, item phases, job
      statuses and job kinds; and the Cloud page's run stages (`RunProgress.stage`), end
      reasons and worker states (`WorkerProgress.state`, `unreadable` included) (their values
      are the codes; entries in `i18n/state_texts.py`, text kinds `run_stage`,
      `run_end_reason`, `worker_state`).
  - The frontends translate what they present: every GUI text; each of those backend texts is
    shown from the catalog entry of its code (`i18n/texts.py` `code_text(kind, code, details,
    fallback)`; the entries are in `i18n/error_texts.py` and `i18n/code_texts.py`), formatted
    with its `details` (each value a message shows is therefore also in `details`), with the
    English `message` only as the fallback for a code without an entry or an entry its details
    cannot fill (both logged; a test fills every entry from its code's details and every coded
    text of the golden files, in both languages). An entry is a template whose `{key}` fields
    name details, or a function of the details returning the template (plural forms, a
    sentence only for some values). An error code whose wording depends on one detail has
    variants `<code>.<value>` (`ERROR_VARIANTS`: the `source` of `media_tool_not_found`, the
    `operation` of `media_tool_failed` and `media_output_unwritable`, the `reason` of
    `choice_invalid` and `cloud_setup_interrupted`, the `part` of `cloud_unreachable`, the
    `keyring` of `credentials_missing`), and so has the plan error `custom_plan_invalid_name`
    (`PLAN_ERROR_VARIANTS`: its `problem`, a code such as `forbidden_characters` with its
    values, `characters` or `max_length`). Values are shown as the language writes them: numbers
    with its separators (`{cost:.2f}`), `{seconds:duration}` as a duration, lists joined with
    commas, booleans as yes or no. The GUI's texts call the backend's batches "jobs" and its
    jobs "transcriptions". The CLI's JSON (requests, responses, progress NDJSON) is a machine
    contract and is not translated; its human texts (the help texts the app passes to argparse
    for `--help`, `cli/parser.py`; the `--progress text` lines and the `credentials` prompts)
    are, through `tr()`; argparse's own fixed strings are not, nor the names of the arguments
    and their choices (argparse quotes them in its errors, which become the `invalid_arguments`
    JSON). The CLI writes `--help` as UTF-8 (a Windows pipe would use the ANSI code page).
    Numbers, dates and durations are
    formatted by one helper per language (`i18n/formats.py`; English `1,234.5`, `2026-11-27`;
    Hungarian `1 234,5` with a no-break space, `2026. 11. 27.`; both 24-hour time; durations
    `1 h 5 min` / `1 ó 5 p`).
  - Not translated: subtitle content and its language codes, Whisper language names in
    requests and responses, file and folder names, log records, the worker's error texts, and
    the GUI entry's `display_unavailable` answer (written before Qt starts, like the CLI's
    JSON).

#### GUI

Built part by part in M6 (`plans/m6.md`); this subsection holds what is built. The last bullet
follows one job through the areas, as the GUI end-to-end flow (§7) does.

- **Entry** (`gui/main.py`, Qt-free): before Qt is imported it checks for a display
  (`gui/display.py`): Windows and macOS always have one. Elsewhere a forced `QT_QPA_PLATFORM`
  (a `;` list) needs one entry that can start: `offscreen`, `minimal` or `vnc` always, `xcb`
  with `DISPLAY`, `wayland*` with a compositor socket where libwayland looks for one
  (`WAYLAND_SOCKET`, `WAYLAND_DISPLAY`, else `wayland-0` in `XDG_RUNTIME_DIR`: Qt passes no
  name to `wl_display_connect`); device platforms (`eglfs`, `linuxfb`, ...)
  never count. Without `QT_QPA_PLATFORM`, `DISPLAY` or `WAYLAND_DISPLAY` must be set. Qt can
  still abort on what the check cannot see (a display variable naming a server that is gone,
  missing xcb libraries such as `libxcb-cursor0`); M8 documents those for Linux users and its
  smoke run starts the build offscreen. Without a display it logs the reason
  (`display_problem`: no display variable, or the forced platforms that cannot start), writes
  it as the JSON `ErrorResponse` with the code `display_unavailable` to stdout (when the
  process has one) and exits 1. Then it
  imports `gui/app.py` and runs `run_gui(make_backend)` (tests inject seeded ports) inside
  `log_to_file(transcribe-ui-gui.log)`. `run_gui` routes Qt's messages to the log
  (`qInstallMessageHandler`, logger `transcribe_ui.gui.qt`) and exceptions escaping a slot
  (`sys.excepthook`) there too, never to stderr; then it builds the backend and activates the
  language of the texts (`gui/language.py`: the `language` setting, the app's catalog and Qt's
  own translation; §3.1 "Language of the texts") before it builds the window. When the event loop ends (normally through the
  close dialog, or without it, as when the session ends) it hides the window, runs the
  executor's `shutdown`, closes it (answers still on their way are dropped, no callback runs
  any more) and waits for the pool threads at most `SHUTDOWN_WAIT_MS` (15 s, one deadline for
  all pools); requests still running then end the process through "Quit now". It then deletes
  the window (`deleteLater` would never run without a loop) and closes the backend's local
  state last (`Backend.close`). However the window is deleted, its executor closes with it
  (`build_shell` connects the window's `destroyed`), so an answer still on its way never
  reaches the window's deleted views. A view deleted while its window lives must call the
  function its view-model's `subscribe` returned (none is today: pages and the set-up wizard
  live as long as the window).
- **Package**: Qt-free modules `executor.py` (the `Executor` port, call handles, channels,
  `SyncExecutor`), `viewmodel.py` (the view-model base: an injected `Executor` and change
  listeners), `activity.py` (the activity panel's view-model), `feedback.py` (error and
  confirmation display data), `writer_lock.py` (the lock notice's view-model), `display.py`,
  `main.py`, `constants.py`, `texts.py` (texts several modules show: page titles, fix labels,
  activity states); Qt modules `bridge.py` (`QtExecutor`), `signals.py` (the bus and
  the start-up hooks), `shell.py` (main window, wiring), `pages.py` (navigation, pages, lock
  notice), `closing.py` (Q30), `dialogs.py`, `activity_view.py`, `messages.py`, `context.py`,
  `language.py`, `app.py`. A test imports the Qt-free modules in a subprocess and checks that PySide6 stays
  unloaded; another parses every module outside `gui/` and refuses any import of `PySide6`,
  `shiboken6` or `transcribe_ui.gui` at any nesting level. Areas (`settings/`, `selection/`, `jobs/`, `review/`, `cloud/`, `download/`,
  `cleanup/`) each get `viewmodel.py` and `view.py` in their part, and an install function that
  `gui/areas.py` calls (one line each): it registers the page, connects the signals the area
  handles and adds its start-up steps.
- **Bridge** (`QtExecutor`): the GUI thread never calls `Backend.execute`. Each request runs as
  a `QRunnable` (auto-deleted by its pool after `run` or on `clear()`; one taken off its queue
  is deleted by the bridge, so finished calls are freed; `run` first waits until `start` has
  returned, since PySide records the pool as parent only then, and is released even if
  `start` fails, in which case the call is forgotten and the error raised) on one of four `QThreadPool`s, by the
  presentation table `ACTION_POOLS` in `gui/constants.py`: `scan` (1 thread: `scan_tree`, sent
  on a `latest` channel, so a new file selection never waits behind uploads or downloads),
  `long` (3 threads: `review_batch`, `run_upload`, `run_download`, `import_custom_plan`,
  `create_retry_batch`, `cleanup_cloud`, `setup_cloud`), `poll` (1 reserved thread:
  `refresh_status`, `reconcile_cloud`) and `short` (3 threads: everything else, so Stop and
  lists never wait behind an upload). `pool_for` picks the pool by request: an
  `import_custom_plan` with `check_only` writes nothing and goes to `short`. The runnable calls `execute` with the call's `CancelFlag` and a sink;
  start, progress events and the response are emitted on a relay `QObject` living on the GUI
  thread through explicit queued connections, so every callback runs later on the GUI thread,
  in emission order, never inside the call that caused it. Requests, events and responses pass
  unchanged; views throttle repaints (`PROGRESS_REPAINT_MS` = 100). An observer sees every
  response first (the writer-lock notice).
- **Handles and cancel** (`PooledCall`, thread-safe; state `queued`, `running`, `done`). A call
  that ran stays `running` until its answer is delivered on the GUI thread (marked before the
  sender's `on_done`), so channels free a key only once its answer arrived; a call that never
  ran (taken off, cleared, cancelled before it started) is `done` at once. `cancel` sets the
  flag; a queued call is taken off its pool with `QThreadPool.tryTake` and completes
  at once as `cancelled` (the backend's `ErrorResponse`); a call dequeued but not started sees
  its flag in `begin` and completes as `cancelled` without calling `execute`; a running one
  stops between steps. `shutdown` (close) calls `clear()` on every pool, completes every call
  still queued as `cancelled`, sets the flag of running ones, and later sends complete as
  `cancelled` without running.
- **Channels** (Qt-free, over any `Executor`): `ExclusiveChannel` (one call per key; a second
  send is refused with None), `LatestChannel` (a new send cancels the key's previous call and
  drops its stale answer by sequence number) and `SingleFlightChannel` (the poll: no tick while
  the previous one is `queued` or `running`; its `state` shows queued as "waiting" and running
  as "refreshing"; a cancelled queued tick frees it at once). Every channel numbers its sends
  per key, so the posted `cancelled` answer of a call taken off its queue never reaches a newer
  send; a key whose latest call answered is forgotten. The backend's locks still guard
  everything (§3.8).
- **Activity panel**: areas start long requests through `Activity.start` (exclusive per key,
  default the request's JSON); each row shows stage, `completed`/`total` and path, Cancel while
  queued or running, Resume (re-sends the same request) after `cancelled` ("Stopped; continue
  any time", never shown as a failure) or an error, Details for an error (its `app_busy` Retry
  resumes the entry), Dismiss once done.
  Each entry keeps its row: repaints update labels and button visibility in place, so a click
  during progress always reaches its button.
- **Bus** (`gui/signals.py`, declared whole by M6.2): navigation (`open_job`, `open_jobs`,
  `open_new_job`, `open_cloud`, `open_cloud_machines`, `open_settings(section)`), job actions
  (`edit_selection`, `open_review`, `run_upload`, `open_download`, `retry_jobs(batch_id, jobs)`
  with the ticked jobs as a tuple of `RetryJob`,
  `clean_up(batch_id, scope)`), cloud actions (`delete_unknown_batch`, `plan_invalidated`) and
  changes (`batches_changed(batch_ids)`, `runs_changed`, `settings_changed`). The shell maps
  navigation to pages; an action signal without a handler (`MainWindow.connect_handler`) shows
  "Not available yet" in the status bar. `StartupHooks` runs registered steps after the window
  shows, by `StartupSlot` (settings, credentials, database, batches, runs, reconcile, refresh)
  then registration order; each step calls `proceed` once when its request answered.
- **Shell** (Q33): a left navigation of four pages (New job, Jobs, Cloud, Settings), each a
  placeholder until its part registers it (`PageArea.add_page` replaces it), the activity panel
  below. Pages are marked whether they need the database (Settings does not).
- **Step header** (`gui/steps/`; Qt-free `viewmodel.py`): the four steps 1 Files, 2 Review &
  upload, 3 Transcription, 4 Download, rendered from the job's `stage` (steps before it done
  with a check mark, its step bold, the step the page shows underlined; `done` is past every
  step; no job yet is step 1). Back and Next show only when the placing page connects them.
  The job page shows it at the job's stage; New job, review and upload, the start dialog and
  download place it on their step.
- **Jobs** (`gui/jobs/`; Qt-free `constants.py`, `display.py`, `commands.py`, `viewmodel.py`;
  widgets `widgets.py`, `view.py`; `install.py`). The Jobs page is a list and a job page.
  - The list sends `list_batches` (backend order, newest first; "Show hidden jobs" sets
    `include_hidden`) at start-up (slot `batches`) and on every `batches_changed`; each row
    shows the creation time (local), id, stage, phase, file and transcription counts, languages
    and labels; Open or a double click emits `open_job`; Hide or Show for the selected job. The
    selection is kept by batch id across reloads (cleared once the job is no longer listed),
    and the table is rebuilt only when its rows changed.
  - The job page (`open_job`) sends `get_batch` and reloads on `batches_changed` naming its job
    (or any job); it shows the step header, the phase, counts, languages and labels, and one
    row per transcription: file, language, kind, status, the reason of a failure (the catalog
    text of `result_error_code`, else the worker's own `result_error`), the file's phase and
    problem, its path. Transcriptions marked `flagged` are bold; while `create_retry_batch`
    is allowed each has a Retry tick box, ticked by default (a reload keeps the user's ticks).
  - Every button comes from `actions` (`ACTION_BUTTONS`, one button per action or group, in
    the actions' order): Edit selection, Continue to review, Delete, Review (`review_batch`,
    `resolve_review`), Upload ("Resume upload" while labelled `upload_unfinished`), Download
    (the four download actions), Retry ticked files, Delete audio / everything from the cloud
    ("Repeat cleanup" for the scope `cleanup_pending` names), Hide, Show. `exclude_items` has
    no button here (the review page). Edit selection, Review, Upload, Download, Retry
    (`retry_jobs` with the ticked jobs; disabled while none is ticked) and the cleanups
    (`clean_up` with the scope) only emit their signal. Hide, Show, Delete and Continue to
    review are sent by `JobCommands` (one at a time per job): Delete asks the backend's
    confirmation and, confirmed, opens the list; Continue to review sends `start_review` and
    emits `open_review`; each change emits `batches_changed`. A load error shows in the page
    with Retry; an action's error opens the error dialog.
- **New job** (`gui/selection/`, step 1 of a job; Qt-free `constants.py`, `tree.py`,
  `marks.py`, `changes.py`, `choices.py`, `scanning.py`, `ticks.py`, `filters.py`, `targets.py`,
  `saving.py`, `editing.py`, `display.py`, `rows.py`, `viewmodel.py`; Qt `models.py`,
  `widgets.py`, `view.py`, `install.py`).
  - Settings: its own `get_settings` at start-up (slot `settings`, after the Settings area's
    load: read-only and without the database; the Settings area owns the form, New job only
    reads the scan options and the languages) and on `settings_changed`; a scan
    takes its extensions and `include_hidden` from them (the request's defaults until they
    answered), the language choosers offer the enabled languages in the user's order (plus a
    reopened job's other languages). A failed load shows in the page with Retry.
  - Folder: Choose folder (the system dialog, native separators) sends `scan_tree` on the scan
    pool on a `latest` channel; the page shows "Scanning… N media files found so far" from the
    progress events, Stop scan cancels it (shown as stopped, not failed), Scan again repeats
    it with the same options and keeps the selection; another folder starts an empty one.
  - Tree: `SelectionTreeModel`, a lazy `QAbstractItemModel` over `ScanIndex` (one pass over
    the flat response: each folder's child folders, its first file and file count; a node is a
    folder's index, files follow the folders), so rows, parents and cells are looked up only
    for what the view shows (`QTreeView` with uniform row heights). The top row is the scan
    root (its whole path), folders before files, in the scan's order. Columns: name with the
    tick box, the subtitles the file already has (its sidecars' languages, forced ones marked,
    "unknown language"; the tooltip lists the subtitle files), kind, size, and the languages a
    selected file is transcribed into. A new scan resets the model. After a preview, a match or
    a language change the model compares a snapshot of what its cells show (`changes.py`: the
    preview's marks, the clicks waiting for an answer, the matches, the languages) and emits one
    `dataChanged` range per folder holding a changed row (a language change only the target
    column of the selected files), so a click costs what it changed, not the tree's size;
    turning the filters on or off, which can dim any row, emits one `layoutChanged`.
  - Selection: each click on a tick box appends one operation and sends `preview_selection`
    with every operation and the scan's `scan_id` on the short pool, on a `latest` channel.
    Tick boxes, partial folders and totals come only from the latest answer; until it arrives,
    the clicked row shows what the click asked for, so a quick second click undoes the first.
    Whether a click includes or excludes: a click still waiting for its answer decides by what
    it asked for; under active filters whose own matches on this scan have answered, a folder
    click looks at the files below it the filters match (`ScanIndex.files_below`, a contiguous
    range): `exclude` when all of them are selected, else `include`, since the operation only
    reaches those files; otherwise a row with nothing selected is included and any other (ticked or partly)
    excluded. Another folder (or a reopened job) drops a preview still on its way. A folder's
    operation carries the filters active then; a file's carries none (an explicit file click
    always selects that file). Clear selection drops every operation. The warnings listed are
    the latest preview's (which start with the scan's), the scan's before it answered.
  - Filters (media kind; subtitle languages with all / any / missing one; name starts with,
    ends with, contains, regular expression, applied 300 ms after the last keystroke) become
    the request's `SelectionFilter`; its own validation refuses a bad regular expression,
    which is shown, and folder ticks wait until it is fixed. While a filter is active, a
    preview of one `include` of the scan root with it (its own `latest` key) tells which files
    match: the others, and folders without a match, are dimmed. Matches belong to their filters
    and their scan: another folder is not dimmed until its own answer arrives (a new scan of
    the same folder keeps the dimming meanwhile).
  - Languages: the job's target languages (none ticked at first; the spoken language is the
    user's choice); a file selected in the tree can have its own ("Same as the job" unticked),
    sent as `item_languages` only for files the latest preview selected (kept for the others
    in case they are ticked again). Unticking "Same as the job" with no job language leaves the
    box unticked until the file's own language is ticked.
  - Create job sends `create_batch` (one at a time; enabled once a folder is scanned, a file is
    selected, the preview has answered without an error and a language is chosen, otherwise a
    hint says what is missing or "Updating the selection…"); the
    `existing_languages` confirmation goes through the generic dialog; a created job emits
    `batches_changed` and `open_job`, and the page starts a new job on the same folder with the
    same languages.
  - Edit selection (`edit_selection`, the shell's handler): the page shows itself and sends
    `get_batch`; the stored composition gives the scan (its own options), the operations, the
    languages and each file's own languages; a job without one starts from an empty selection
    on its scan root with the settings' options and the job's languages. The step header shows
    the job's stage; "Save selection" sends `edit_batch`; "Start a new job instead",
    `open_new_job` and choosing a folder go back to a new job (on that folder, with an empty
    selection). Leaving the edit (or a new job's folder) drops a load of the job still on its
    way, and a create or save sent before (also its retry from the error dialog) and answered
    later only emits `batches_changed`; choosing the folder already shown keeps the selection
    and a create in flight for it. Scans run on the scan pool and previews on the short
    pool, so New job works while uploads fill the long pool.
- **Cloud** (`gui/cloud/`; Qt-free `constants.py`, `ports.py` (the `Timer` and clock ports,
  the outlet), `display.py`, `plan_display.py`, `commands.py`, `runs.py`, `poll.py`,
  `run_actions.py`, `machines.py`, `start_form.py`, `start.py`, `status.py`, `area.py`; Qt
  `startup.py`, `timers.py`, `run_panel.py`, `sections.py`, `start_view.py`, `status_view.py`,
  `view.py`, `outlet.py`, `install.py`). The Cloud page shows a check banner, the current run,
  the cloud machines and the run history; the status bar line and the start dialog belong to
  the area too.
  - What the page shows is the run list (`list_runs`, database only, latest-wins channel, so
    only the last load sent is shown): the run that is not final, else the last run with its
    outcome; it loads at start-up (slot `runs`), after every check and every action that
    changes a run, and on `runs_changed` from another part. The current run shows its stage,
    Runpod's pod status, the worker's state, the file and job it transcribes (the job a link
    to its page), done/total, a progress bar (`progress_fraction`), the remaining time counted
    down each second to `estimated_finish_at` ("Finishing" at 0, never negative), elapsed time
    and cost as of the last check (never extrapolated), the deadline, the last check's time,
    the outcome, the worker's error and a deadline stop's `stop_error`; a kept worker log opens
    with the system's viewer.
  - The poll (`poll.py`): while the list holds a run that is not final, or right after a
    launch, `refresh_status` every `REFRESH_INTERVAL_S` (300 s) on the `poll` pool and a
    single-flight channel; a tick is skipped while one is queued or running ("Waiting to check
    the cloud" / "Checking the cloud"); the timer stops once a load holds only final runs.
    "Refresh now" checks at once and restarts the interval. A check's `runs_busy`,
    `state_busy` or `cloud_unreachable` shows as "Last updated <time>; retrying at the next
    check", another error in the page's banner with its fix and as a status bar link, never as
    a dialog. Each check
    reloads the run list (even a failed one) and emits `runs_changed`, and `batches_changed`
    for the batches it reported. A run that turns final flashes the window in the task bar
    (`QApplication.alert`) and emits `batches_changed` for its covered batches. The timer is a
    port (`QtTimer`; tests fire a manual one), so no test sleeps.
  - Start-up (`startup.py`): `credentials_status` tells whether a Runpod key is set
    (`key.py`, `RunpodKey`; asked again each time the page is shown); with one (or when that
    is not known, or the keyring failed while reading) `reconcile_cloud` runs (slot
    `reconcile`); `refresh_status` (slot `refresh`) runs with or without it (§3.9 "Roles").
    Without the key (R2-only) the page shows a banner with the reason and "Open accounts";
    Start transcription, Stop and Cancel and the machines check are disabled with the hint
    "Needs a Runpod API key on this computer." The poll also runs while the last check's
    `awaiting_results` is true, without a run of this computer; `batches_changed` (an upload,
    an adoption) starts a stopped poll, and Refresh now during a check checks once more after
    it. The settings area registers first, so in the slot `reconcile` its `cloud_setup_status` comes
    before the machines check. The cloud area asks `credentials_status` itself (a local read)
    instead of reading the settings area's view-model, so the areas stay independent.
  - Stop and Cancel (Q34; `run_actions.py`): "Stop waiting" (Q34's Cancel, named apart from
    the confirmation dialog's own Cancel) while the stage is one of `WAITING_RUN_STAGES`
    (`launching`, `waiting`; `runs/constants.py`, shared with the question's text), "Stop"
    afterwards; both send `cancel_worker`, whose backend confirmation the generic dialog shows
    without its details table (the question names what it stops; the same for the machines'
    stops); Stop runs on the `short` pool, so a full `long` pool never delays it. Every answer
    of a stop except a declined question and `cancelled` reloads the run list, a failed one
    too (it may have changed the run before it failed). Start transcription is disabled while
    the shown run is not final. Closing the window never stops a run: the shell reads
    `GuiContext.run_active` (the run list's) when asked, so the close question adds its cloud
    note.
  - The start dialog (`start.py`, `start_view.py`; step 3 of the step header): opening it
    plans (`plan_launch`, latest wins); it shows the transcriptions, jobs, audio length, cost
    range with the price per hour (and the fallback price's note), the time limit, one row per
    job (this computer's or another computer's; the latter with "Delete from cloud", which only
    emits `delete_unknown_batch`; `plan_invalidated` plans again while it is open), and the
    objects the machine skips. The wait for a machine offers the usual waits within the plan's
    `WaitBounds` (default selected); test mode is under "Advanced" and plans again (its
    fingerprint differs). Start sends `launch_worker` with the plan's fingerprint; a changed
    plan comes back as `needs_confirmation`, whose plan (details) the dialog shows with "The
    work changed since you saw it" and whose `set_to` the next Start confirms. Errors
    (`cleanup_pending` with "Open the job", `run_active`, cloud errors) show in the dialog with
    their fix and Retry, which sends the failed request again (the plan or the launch); a fix
    that opens a page closes the dialog first. Start is disabled while the plan has nothing to
    transcribe, while a Start is in flight, and while a listed run is not final (with a hint;
    `plan_launch` has no run check, and the backend would refuse the launch with
    `run_active`). The dialog may be closed while the machine starts: the launch's answer
    still reaches the run list (and starts the poll); a changed plan answered then reopens the
    dialog by itself with that plan (the model's `reopened`; nothing started, and the user
    meant to start; there Start is not the default button and Close has the focus, so a stray
    Enter closes the dialog and starts nothing), an error opens the error dialog; a dialog
    closed again after that is reopened the same way. A dialog the user opened again shows a
    note while an earlier opening's Start is in flight, and when it answers (started: "the
    Cloud page shows it", and it plans again; a changed plan: it plans again); that answer
    never closes it. "Delete from cloud"
    emits its signal after the click returns, and the table is rebuilt only when a row
    changed.
  - Cloud machines (`machines.py`): `reconcile_cloud` at start-up and on Check; each entry's
    kind, name, Runpod status, run and role (current run's, or left over; past its time
    limit). A straggler or an overdue entry has Terminate (`terminate_pod`) or Cancel
    (`cancel_queued_pod`, labelled "Stop waiting"), each asked through the backend's
    confirmation, then checked again, also after a failed stop. A check asked for while one is
    in flight is sent again once that one answered.
    `attention` shows a banner with "Show cloud machines" (`open_cloud_machines` scrolls to the
    list) and a status bar link; a failed check shows the page's banner with Retry.
  - Run history: the listed runs with start, run id, stage, outcome, elapsed time and cost (to
    the end, else the last check), test mode and the kept worker log.
  - Status bar line (`status.py`, `status_view.py`; a permanent widget of the window's status
    bar): the run that is not final (stage and countdown; to the Cloud page), a machine needing
    attention (to the machines list), a failed cloud check (of the machines, or of the runs
    unless it is retrying), and each job whose long step is queued or running in the activity
    panel (to its job page), as links. Changes of the activity panel repaint it at most every
    `PROGRESS_REPAINT_MS`; run and machine changes repaint at once.
  - Decisions: the GUI reuses the jobs area's read-only table helpers (`gui/jobs/widgets.py`)
    without editing them; the start dialog is reached from the Cloud page (the job page has no
    action for it, since a launch covers every uploaded job).
- **Download** (`gui/download/`; Qt-free `constants.py`, `form.py`, `display.py`,
  `loading.py`, `export.py`, `running.py`, `viewmodel.py`, `state.py`; widgets `panels.py`,
  `results.py`, `widgets.py`, `view.py`; file dialogs behind the `FileChooser` port in
  `chooser.py`; `install.py` with the Qt `Deferral`). `open_download`
  opens the job's download dialog, step 4 of the step header: non-modal and one per job,
  kept while the window lives, so it reopens with the user's choices and the window stays
  usable while it is open.
  - Jobs of other computers (`others.py`, `others_display.py`, `others_view.py`; §3.9
    "Adoption"): a section of the Cloud page, below the current run, added by the download
    area through `CloudPage.add_section`. "Look in the cloud" sends `list_cloud_batches`
    (latest wins; only when asked) and lists the jobs with `known` false (files, audio length,
    state); Download sends `adopt_cloud_batch` (exclusive per job), whose confirmation the
    generic dialog shows without its details table. Once adopted, the jobs reload
    (`batches_changed`), its download dialog opens (`open_download`), the Cloud page checks
    the cloud (`check_now`) and the list loads again; an error opens the error dialog (Retry
    re-sends) and also reloads the list.
  - The first opening loads `get_settings` and `get_batch` side by side and, once both
    answered, pre-fills the choices from the placement defaults (strategy, output folder,
    folder count, overwrite), with `mirror` for a job labelled `adopted` (its originals are on
    another computer, so `next_to_media` is refused); only then does it plan. Every later
    opening loads `get_batch` (the stage) and the plan. A settings answer that arrives after the user changed a choice
    pre-fills nothing. Each change of a choice sends the plan again on a `latest` channel:
    `plan_download` for a strategy, the `check_only` import for a custom plan (on the `short`
    pool: the bridge picks a pool by request, `pool_for`); folder count steps and level ticks
    wait `PLAN_DELAY_MS` (250 ms) for the next one. A plan of choices changed since is
    cancelled (`plan_download` and a checked import stop at their next file system question)
    and its answer ignored. A choice the request needs but lacks (an output folder, an
    imported plan, a folder level) shows a hint instead of the table and sends nothing;
    `next_to_media` sends no output folder. A plan's rows, errors and summary are built once,
    when it arrives; a closed dialog does not repaint and refreshes when shown.
  - The folders kept in the name: the closest ones (`folder_count`) or chosen levels
    (`folder_levels`), offered from `PlanDownloadResponse.folder_levels` (§3.5), each with
    some of its folder names. With levels chosen but none ticked the plan is still asked
    for, only to list the levels.
  - The plan table shows each job's file, language, target name and folder, what happens
    (planned outcome; `not_finished` is "Not finished yet", `skip_folder_missing` "the
    original folder was not found") and the rename reasons; a summary line counts them (the
    placed count always, the others when there are some). Plan
    errors are listed from the catalog; Download is enabled only for a plan of the current
    choices without errors.
  - Download runs `run_download` (or `import_custom_plan` without `check_only`) as an
    activity keyed by the job (`running.py`), so it shows in the activity panel and in the
    dialog with Cancel, Resume (the same request) and Details. A `needs_confirmation`
    answer (files would be replaced) removes the entry and asks over the dialog; Confirm
    starts the confirmed request; Details opens the error dialog over the download dialog.
    Any answer of a download emits `batches_changed` (outcomes may have been recorded) and
    reloads the dialog once (it ignores its own signal), which then shows the last download's
    counts and its `failures`, each with the catalog text of its code; a download with
    failures shows its dialog again (raised; it asks Qt not to activate it, which may still
    take the focus depending on the platform), since the activity
    panel's "Finished" would hide them. The dialog has no default button, so Enter in the
    output folder only ends the typing.
  - Custom: Export plan (save-file dialog → `export_custom_plan` with `path`), Import plan
    (open-file dialog → the `check_only` import; its problems listed per plan error), Check
    again after more edits, then Download. A load error shows above the plan with a Retry
    that loads again; an export error shows with the custom plan, with a Retry that exports
    to the same file, and is forgotten when a new export starts or the strategy changes.
- **Retry and cleanup** (`gui/cleanup/`; Qt-free `constants.py`, `display.py`, `panel.py`,
  `retry.py`, `viewmodel.py`, `area.py`; Qt `view.py`, `install.py`; no page of its own): the
  handlers of `retry_jobs`, `clean_up` and `delete_unknown_batch`. `install.py` gives them to a
  `CleanupHub` (a `QObject` child of the window) and connects the hub's methods, so the
  window owns them and PySide drops the connections with it; the outlet holds the window only
  weakly, so nothing keeps a closed window and its context alive.
  - Each request runs in the activity panel under one key per job (`("cleanup", batch_id)`), so
    a retry and a cleanup of one job never run at once; a second one is refused with a status
    bar note.
  - Retry: `create_retry_batch` with the ticked jobs; the panel shows the copy (`upload_item`
    progress), Cancel and Resume (re-sending resumes the same retry job). Its answer emits
    `batches_changed` with the source and the retry job and opens the retry job. An error stays
    in the panel with the catalog text (`audio_deleted`: the audio was deleted since the page
    showed Retry) and reloads every job (`batches_changed(())`).
  - Cleanup (audio only, everything, the repeat of a `cleanup_pending` one, another computer's
    job): `cleanup_cloud` is sent first without a confirmation, titled "Checking the cleanup of
    job …" in the panel; its `needs_confirmation` opens the cleanup question (`CleanupDialog`): a
    heading naming the job and what is deleted, each confirmation's catalog text (the codes of
    `WARNING_CONFIRMATIONS`, today `cleanup_not_downloaded`, in bold), "This cannot be undone.",
    an accept button that names the deletion (Delete audio, Delete everything, Delete from the
    cloud) and Cancel as the default button with the focus, so Enter never deletes. What is
    deleted (heading, button, the panel title once confirmed, and `plan_invalidated` instead of
    `batches_changed`) comes from the confirmation codes (`cleanup_unknown_batch`,
    `cleanup_everything`, `cleanup_audio`); a later round without such a code keeps the earlier
    round's, and the signal decides only without any. When the codes and the signal disagree,
    the deletes emit both `plan_invalidated` and `batches_changed`. The question opens over
    the modal dialog in use (the start dialog), else over the window, and is deleted once
    closed. The checking entry leaves the panel (nothing was deleted). Confirm re-sends the
    request with each `set_to`; the deletes ("Deleting …") run in the panel with progress,
    Cancel and Resume. Every answer of the confirmed request (a stopped or failed one may have
    deleted some, or left `cleanup_pending`) emits `batches_changed` with the job, or for
    another computer's job `plan_invalidated`, so the start dialog plans again. A refusal of the
    unconfirmed request opens the error dialog over the dialog in use, like the question (its
    `app_busy` Retry re-sends it); `retry_copying` gets "Open the retry job" (its
    `retry_batch_id`). A refusal of the confirmed one stays in the panel, the same fix under
    Details.
  - Late answers: the handlers count the user's moves: the navigation signals
    (`NAVIGATION_SIGNALS` in `gui/signals.py`, next to `ACTION_SIGNALS`; a new one goes there),
    the navigation list's row, the Jobs page's stack ("All jobs" switches it without a signal)
    and the start dialog opened (by the program; a restored minimized window is not a move) or
    closed. A question or a refusal that answers after a move
    opens nothing (a note: nothing was deleted); a retry that answers after one does not open
    its job (a note says it is on the Jobs page). A re-send from the error dialog keeps the
    count of the original request.
  - Decisions: the cleanup has its own question instead of the generic confirmation dialog (that
    one makes Confirm the default button and lists the raw details); the unconfirmed request
    runs in the panel too, so it shows (and can be cancelled) while the long pool is full.
- **Review and upload** (`gui/review/`, step 2 of a job; Qt-free `constants.py`, `options.py`,
  `draft.py`, `folders.py`, `sender.py`, `loading.py`, `index.py`, `editing.py`, `commands.py`,
  `uploads.py`, `viewmodel.py`, `display.py`, `status.py`; Qt `table.py`, `widgets.py`,
  `header.py`, `view.py`, `install.py`). The page lives on the Jobs page's stack (`open_review` shows it
  there; Back to the job and the job page hide it); `run_upload` (Upload or Resume upload on
  the job page) starts the upload without leaving the page it came from.
  - Loads: `get_batch` (the step, the allowed actions, the folders), then `review_batch` only
    while the job allows it (else "No file of this job waits for review"), then `get_batch`
    again, since a probe can change what the job allows. The page shows the probe's progress
    with Stop checking (shown as stopped, not failed; Check again continues with the files not
    checked yet), "waiting" while the review is queued behind other calls, and a load error
    with Retry. A review still queued on its pool does not hold the job (a decision of the main
    session, not a product answer): choices stay open; Apply and Remove pre-empt it (it is taken
    off its queue, the action runs at once on the short pool, and the same review is queued again
    after it; actions always go before reviews still waiting); Upload drops it first (it would
    run first or meet the upload's lock) and shows the wait, and the end of the upload loads the
    review again. Whether a review started is read from its call's state, not only from its
    start signal. Apply's answer is shown at once (it is newer than nothing; a review still
    waiting replaces it later); files removed meanwhile show "Removed from the job" with no tick
    or choice, and the counts and hints leave them out until the next review.
  - One at a time: `review_batch`, `resolve_review` and `exclude_items` hold the job's lock
    (`batch_busy` is never waited for), so `SerialSender` sends them one after the other: a new
    review replaces a waiting one and cancels a running one (its answer is dropped), an action
    waits. A review is not sent while the job's upload runs (shown as waiting for it). Each open
    of a job and each filter change starts a new generation; answers of an older one, or about
    another job, are dropped (an Apply's still emits `batches_changed`), and a retry from the
    error dialog keeps its first send's generation. A hidden page cancels its review and sends
    nothing: a load asked for while it is hidden (`batches_changed`, the answer of an Apply or a
    Remove sent before) only marks it stale, and it loads once when shown again; only a call
    already in flight when it hid can still answer. Leaving it with choices not applied says so
    in the status bar (the job page's Upload ignores them).
  - Views: the filters (the job's languages as tick boxes, shared with New job; "All folders"
    or a folder below the job's folder that holds a file in review) go into the request's
    `filters`. Three tables: Embedded subtitles, Audio tracks (a row per file and language)
    and Needs attention (a row per file: its problem's catalog text, or the languages without a
    choice, or nothing to transcribe or extract). A row's combo box lists the choices of the
    transcription: transcribe each probed audio track (one "Transcribe" for an audio file or a
    single track) and extract each text subtitle stream; picture-based streams are only listed
    in the details ("transcribe only"). "Transcribe all shown" and "Extract all shown" choose
    for every row of the embedded-subtitle view the filters show, from the backend's
    `suggestions` (§3.3 "Views"), so a choice can be made per folder or per language.
  - Scale (tens of thousands of files): what a row shows that only a new review changes
    (`index.py`: file, folder, language, the choices with their labels, the stored choice,
    details; where each transcription and file is; each transcription's choices computed once
    and shared by the tables) is built once per review, the folder filter's choices only when the
    job's files change. Each table is a `QTableView` over a `QAbstractTableModel` (`table.py`) that reads a row's state (choice shown, status, tick)
    only when the view paints it, is reset only by a new review, and gets `dataChanged` only for
    the rows an edit changed (`EditSignal`); rows have a fixed height. The choice column has no
    widget per row: a delegate creates a combo box when a cell is edited (a click) and hands the
    chosen `ReviewChoice` itself to the view-model, so the mouse wheel cannot change a choice it
    passes over (the editor ignores the wheel; the folder filter's combo box takes it only while
    focused). An open editor is closed once choices cannot change, and a pick refused then is
    said in the status bar, never lost silently. A new review keeps the current row (by
    transcription) and the scroll position; the columns are sized to their contents only on a
    job's first load. Progress repaints touch only the status line, the buttons and the upload
    box.
  - Choices wait in a draft (per file and language, in the order made; choosing the stored
    choice again drops the change) until Apply choices (`resolve_review`); Discard changes drops
    them. Choices change only while nothing holds the job (a check, a save, its upload), so what
    is shown is the latest review (a review only queued does not hold it: the new review drops
    the choices it no longer lists or its files no longer allow, and files removed meanwhile
    cannot be chosen for); the filters are locked while choices wait ("Apply or discard
    your changes before changing the filters or uploading"), and a review that no longer lists a
    transcription (its file uploaded or removed meanwhile), or views that go away, drop its
    choice and tick, so Apply never sends a stale one. Removed files lose theirs at once. The views come from the answer when it is the page's current, unfiltered view, else
    they are loaded again (`resolve_review` answers unfiltered). Remove ticked files from the
    job sends `exclude_items` through its backend confirmation; Remove tick boxes are per file.
  - Buttons follow `actions` (Upload, "Resume upload" while labelled `upload_unfinished`;
    Remove while `exclude_items` is listed; Apply while `resolve_review` is) and are disabled
    while a check, a save or the job's upload runs, while choices wait for Apply, or while files
    need attention; one hint says why. No button is a default or auto-default button, so a
    stray Enter starts nothing. Upload, Resume upload and the error's retry share one
    precondition (`upload_enabled`), are disabled with the hint while it fails, and check it again
    when clicked (a refusal is said in the status bar); Check again too. "Transcribe all shown"
    and "Extract all shown" are disabled while the embedded-subtitle view is empty.
  - Upload (`UploadTracker`): `run_upload` in the activity panel, one per job (a second start
    says "already running" in the status bar). The tracker follows its entries in the panel
    (each progress event comes with a change of the panel's view-model) and keeps the latest
    event per stage tagged with the call it came from, so a Resume shows only its own progress.
    The page's upload box shows the files uploaded (`upload_item`), the current object's bytes
    with a progress bar (`upload_bytes`, dropped once the next file's event arrives), Cancel upload, Resume upload (a new start of the same
    request) and Details of an error; once finished, the outcome: ready, extracted only,
    or the files back in review by name (also named in the status bar). Every end (also a stop
    or a failure) emits `batches_changed`, which reloads the job page and the review (the files
    the upload sent back to review are probed again there; their choices stay when they probe the
    same).
  - Repaints: at most every `PROGRESS_REPAINT_MS`, only while the page is visible (repainted
    once when it shows again); the tracker tells only about the job that changed.
- **Writer lock**: the `database` start-up step sends `list_batches` (read-only) to take the
  lock. While another process holds it (`app_busy` from any response), a page that needs the
  database is replaced by a notice with Retry; Settings stays usable. A Retry that gets the
  lock emits `batches_changed(())` and `runs_changed` so pages reload.
- **Errors and confirmations**: one error dialog for every code (the catalog text of the code,
  the backend's message only for a code without an entry; collapsed Details with
  the code, the details as a key/value table and one table per detail that lists objects, "Open
  log folder"; `internal_error` adds "see the log"). `ERROR_FIXES` adds a button: credentials
  codes → Settings accounts, cloud setup codes → Settings cloud setup, `cleanup_pending` → the
  first batch in `batch_ids`, `app_busy` → Retry (re-sends the failed action, if any, and
  probes the writer lock, so the notice clears too); unknown codes render generically. One
  confirmation dialog for every `needs_confirmation` (each confirmation's catalog text);
  Confirm re-sends the same request with
  each `field` set to its `set_to` (`feedback.confirmed_request`, validated as a `Request`).
- **Closing** (Q30): with calls pending or a run not final (`GuiContext.run_active`, set by the
  cloud page), the window asks "Stop and close?" (with the cloud-machine note while a run is not
  final). On Stop and close it runs `shutdown` and, while calls are still running (queued ones
  are cancelled at once), a modal "Finishing the
  current step" dialog (Esc does not close it) whose "Quit now" kills the running
  ffmpeg/ffprobe processes (`media.runner.kill_running_tools`; on Windows their job object ends
  them with the process), flushes and closes the root log handlers and ends the process with
  `os._exit(0)` (`sys.exit` would wait for pool threads; every step is idempotent). The window
  closes once every call has delivered its answer.
- **Settings** (M6.3, `gui/settings/`; needs no database, so it stays usable under the
  writer-lock notice). Qt-free: `viewmodel.py` (preferences), `accounts.py`, `cloud_setup.py`,
  `about.py`, `startup.py` (first start), `draft.py`, `problems.py`, `status.py`, `models.py`,
  `hooks.py`, `restart.py`, `texts.py`, `account_texts.py`; Qt: `view.py` (the page: tabs
  Preferences, Accounts, Cloud setup, About; `open_settings(section)` selects one), its section
  views, `wizard.py`, `notice_view.py`, `install.py` (`install_settings(window)`, one line in
  `gui/areas.py`).
  - Preferences: one form over the whole `Settings` (languages with on/off and a word timing
    mark, the stored order kept and a newly enabled language added last; video and audio
    extensions typed on one line, split at spaces, commas and semicolons, sent as typed;
    hidden entries; ffmpeg/ffprobe and the output folder as text fields with Browse (native
    dialogs, the OS's separators; empty or blank means none); the placement defaults, all
    fields always editable; the language: System language, English, Magyar, the last two in
    their own language). Save sends `update_settings` with the whole document; Undo changes
    restores the stored settings. The form's document is validated as the backend validates
    a request document (`api.schema.parse_request`), so a refused one gets exactly the
    `invalid_request` the backend and the CLI give for it, without a round trip; each refused
    field shows a translated hint under it (the problem's English text is not shown), a
    problem of no field (the extension overlap) shows above Save, and editing a field clears
    its hint. A refused save (local or from the backend) never changes what was typed; a
    backend error other than `invalid_request` opens the error dialog. A failed load shows its
    error with Retry and Restore defaults (`reset_settings`, §3.8). A save emits
    `settings_changed`.
  - Language restart (Q32): after a save that changed the stored language to one that
    resolves to another language than the one shown, "Restart now to change the language?"
    (Restart now / Later); a later save that keeps the language does not ask again. `system`
    resolves with the OS language read once at start-up (`gui/language.py` `use_language`
    returns `StartLanguages`; `GuiContext.system_language`), never again on the GUI thread. Restart now sets `GuiContext.restart` and closes the window through the
    close handling (Q30); keeping the window open there withdraws it
    (`CloseGuard.when_kept_open`), "Quit now" quits without a restart. `run_gui` starts the GUI
    again only after the backend is closed, so the new process can take the writer lock: the
    one-file build runs itself again (with `PYINSTALLER_RESET_ENVIRONMENT=1`, so it unpacks
    its own files), from source the interpreter runs `transcribe_ui.gui.main.run`; a start
    that fails is logged.
  - Accounts: a masked field per credential (the endpoint and bucket too, §3.6; labels and the
    hints of refused values are the CLI's, `i18n/credential_texts.py`), empty with a
    placeholder; where each is set (keyring, environment variable, not set) from
    `credentials_status`; the keyring's state when it is not usable; the note that the Runpod
    key is optional (§3.9). Save sends `store_credentials` with only the fields typed into,
    validated like the preferences (a refused value keeps what was typed and shows a hint);
    after a store the fields are emptied and the status is read again. The fields are disabled
    while a store is in flight, and a store drops only the values it sent. Typed values stay in
    the view-model as `SecretStr` until then (the wizard forgets those not stored when it opens
    and closes, so no value outlives its field); display data name credentials and sources,
    never values. Test sends `check_credentials` and shows one line per service (the catalog text of
    its code).
  - Cloud setup: Check (`cloud_setup_status`: a row per object with its state, outdated
    fields named), Set up / Update (`setup_cloud` in the activity panel under the key
    `setup_cloud`, then a new check; the summary of what was created, updated or left, until
    the next check), the error of a check or setup in the page with Details. A check asked
    for while one runs waits for it; a setup that ends while a check runs gets a check of its
    own (the earlier one's answer is dropped, never shown), and the callers wait for that one. Without a Runpod key the page is
    unavailable and says that another computer with the key starts the transcription.
  - About: the app and data format versions (`get_app_info`; the window title gets the app's
    version) and the log folder with "Open log folder".
  - Start-up steps: `get_settings` and `get_app_info` (slot settings), `credentials_status`
    (slot credentials), and with a Runpod key `cloud_setup_status` (slot reconcile, after the
    database steps). The set-up wizard (a dialog over the Accounts and Cloud setup sections:
    new widgets on the same view-models) opens once per session: at accounts while an R2
    credential is missing, else at the cloud setup when an object is missing or outdated
    (`duplicated` alone is left to the page). It can be closed ("Set up later"); without a
    Runpod key accounts is its last step. On accounts, Next / Finish first stores what was
    typed and moves on once it is stored; a refused value keeps the step open with its hint. A notice above the pages stays while the cause
    lasts: R2 incomplete (with the Runpod note; Open accounts), the cloud setup missing or
    outdated (Open cloud setup), or a failed cloud check (its text; Retry), each with Hide (for
    the session).
- **A job through the window** (M6.11; the GUI end-to-end flow, §7). Settings pre-fill New job:
  the scan options, and the languages in the user's order (the job's `languages` follow
  `enabled_languages`, not the order of the ticks; a user who wants English first unticks
  and ticks Hungarian again in Preferences, which puts it last). New job scans (`scan_tree` draws the scan id), previews on the kept
  scan and creates the job on it (`scan_id`, so no second scan), confirming
  `existing_languages` through the generic dialog; the job page opens. Continue to review
  opens the review page, which checks the files (`review_batch` once, as long as the job
  allows it); Upload runs in the activity panel. The Cloud page's Start transcription plans
  and launches; the poll follows the run on its timer until the run is final, and each check
  reloads the run list and the job pages. On the job page, Download opens its dialog (mirror
  into a typed folder), Retry creates the retry job from the ticked transcriptions and opens
  it, and the cleanup buttons ask the cleanup question before deleting. Between these steps
  the GUI sends only requests that change nothing the steps depend on (loads, previews,
  plans, the credential and setup checks, a start-up cloud check that finds nothing) and asks the backend's confirmation where the CLI flow confirms at
  once; the steps' requests give the CLI flow's responses byte for byte.

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
- The GUI uses a lazy `QAbstractItemModel` over the scan result (virtual rows: an index carries
  its node number, folders by index), which scales to tens of thousands of nodes. Its check
  states are never derived from children in the GUI: every tick box, folder state and total
  comes from the latest `preview_selection` answer (§3.1 "GUI", "New job").

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
  The phases are not a strict line (planned on the requirements "Subtitle download options" and "Runpod control", `plans/m5.md` §1):
  a batch in `composing` or `reviewing` can be deleted; finished subtitles can already be
  downloaded in `ready` and again in `downloaded` (extracted ones even in `cleaned`); a batch
  reaches `downloaded` once it has `_done.json` (or is extract-only) and every job has a placement
  outcome (placed, or skipped as an existing file, a missing folder or an empty result), in one
  download or several; an item whose source changed during `uploading` goes back to review on its
  own (or is excluded) while the batch stays `uploading` (excluding the last item is refused); a
  batch whose upload completes while a run is active is published at once but is not in that
  run's lease, so the worker never gets work its deadline and estimate did not include: it
  waits for the next start (`waits_for_current_run`, §3.9 "Run scope"); cloud cleanup is
  possible from `uploading` on (below). `audio` cleanup sets an "audio deleted" flag without changing the phase;
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
  - Both scopes are refused while a live run lease lists the batch (§3.9 "Run scope": that run
    may be working on it; `run_active` names the lease), so no worker writes after a cleanup.
    The check reads R2 alone, so it works without the Runpod key. A batch no lease lists (one
    published after the run started, or still in `uploading`) can be cleaned up during a run;
    an `uploading` batch's cleanup (`everything` only, requirements "Cloud cleanup") also aborts
    open multipart uploads.
  - `everything` order (resumable): manifest first (so no worker can pick the batch up again),
    then `_done.json` and results, then audio; a crash in between leaves only objects under the
    batch prefix, which the next attempt removes. If results were not downloaded yet, the
    response asks for a second confirmation (only the Global Volume backup would remain).
  - `audio` is refused while the batch has jobs without a result (the worker would fail them);
    after a crashed run, `everything` is the way out. Without audio, Retry is no longer
    possible (a retry request is refused); the UI says so before confirming.
  - Both scopes are refused on a batch while one of its retry batches is still `uploading`
    with copies pending (the error names the retry batch).
  - A batch this database does not know (Q28) can be deleted with `everything` once its
    manifest reads; its deletes put the manifest last (below).
- **Readiness marker**: the manifest is uploaded last, after every audio object is verified.
  A manifest present in `batch_metadata/` therefore always means "ready"; the worker never sees
  half-uploaded batches.
- **Local state**: SQLite (WAL mode, single file) in the user data dir through SQLAlchemy 2.0
  ORM, one writer process at a time; rows store Pydantic models as JSON plus indexed status
  columns. Temp audio goes to `user_cache_dir`, one file at a time. History is kept; finished
  batches can be hidden. Details: §3.8.
- **Implementation** (`transcribe_ui/batches/`; requests `create_batch`, `edit_batch`,
  `delete_batch`, `start_review`, `review_batch`, `resolve_review`, `exclude_items`,
  `list_batches`, `get_batch`, `set_batch_hidden`; all open the database). Models: `classes.py`
  (requests), `responses.py`, `payloads.py` (the JSON payloads of the `batches`, `items` and
  `jobs` rows; the M5.2 tables are used as they are).
  - **Composition.** `create_batch` and `edit_batch` carry the scan options, an optional
    `scan_id` (the kept scan is reused as in `preview_selection`), the selection operations
    and the batch's target languages, plus optional `item_languages` (other languages for
    single files, matched with the path-flavour rule; an entry naming a file outside the
    selection is `path_not_selected`). The backend resolves the selection itself, so the
    request alone decides (§3.1); an empty selection is `nothing_selected`. A job for a
    language the file already has a sidecar subtitle in (forced ones do not count) needs
    `confirm_existing_languages`; the confirmation lists the files by their `/`-separated path
    below the scan root (details are not path-normalised). Items are listed in scan order.
    Both keep the selection in the batch payload (`StoredComposition`: the scan options and the
    operations as their JSON, `languages`, `item_languages`; not `scan_id` or the confirmation).
    Stored payloads use their own plain models (strings, JSON values, unknown keys ignored, no
    request validators), so a later change of the request models never makes a stored batch
    unreadable. `BatchDetail.composition` (`BatchSelection`, the request models) maps it at the
    edge: a frontend reopens "Edit selection" by sending it back as an `edit_batch`; a stored
    selection those models no longer accept (a language dropped, say) is shown as `null`
    (logged), and nothing else of the batch fails. A retry batch (and a batch stored before the
    Alembic revision `composition_and_run_report`) has none (`null`). The summary reads
    `cleanup_pending` straight from the payload, without validating the rest of it.
  - **Ids and locks.** The batch id comes from the injected clock in `Ports.local_zone` (None:
    this computer's zone, read at each use, so DST applies); the free id is picked inside the
    transaction that inserts the batch (`BEGIN IMMEDIATE` serialises two creations in one
    second), so `create_batch` takes no batch lock: nobody can know the id before it commits.
    Every other changing request holds the batch lock and changes the batch conditionally:
    phase moves by compare-and-set, other changes by `touch_batch` (`UPDATE ... WHERE phase
    IN (...)`). A request in the wrong phase gets `wrong_phase` (details: phase, allowed);
    `phase_conflict` stays the error of a lost race. `start_review` on a batch already in
    `reviewing` is a no-op.
  - **Item phases**: `composing`, `reviewing`, `reviewed` (probed, every job resolved),
    `uploaded` (written by the upload, M5.10). An item in `reviewing` or `reviewed` is "in
    review": its choices can change. The review is complete when no item in review is
    unresolved; the upload moves the batch on from there.
  - **Probing.** `review_batch` stat-s and probes every item in `reviewing` (also on a later
    call, so a file that changed is caught; the probe cache makes this cheap): one transaction
    per item, ffprobe outside it, progress per item, the cancel token between items, so an
    interrupted review continues with the rest. The probe cache (`probe_cache.py`) keeps one
    row per path; a row it cannot read (another payload shape) is a miss and is overwritten.
    The file is stat-ed again after ffprobe: when its size or mtime changed meanwhile, the
    result is neither cached nor used and the file is probed once more; changing again makes
    it an item problem (`source_changing`). The item's recorded size and mtime become the
    probed ones (the upload's source-unchanged check compares against them). A missing file
    (`source_missing`), a failed or timed-out probe (`probe_failed`) or a file that keeps
    changing is an item problem, not a failed request; a missing tool, broken settings or
    cancel fail the request.
  - **Pre-selection** (`choices.py`): a job transcribes by default (the user asked for a
    transcription); extracting an embedded subtitle is chosen in the review. Track: the one
    tagged with the job's language (tags read like sidecar names, `hun` = `hu`), else the
    default-flagged one, else the first. An audio file has no track choice (uploaded as it is,
    the manifest's audio item has no track); it is not in the track view. A file without audio
    extracts a text subtitle tagged with the job's language when it has one, else the job stays
    unresolved. Choices made earlier are kept when a file probes the same as before.
  - **Views.** `review_batch` returns `subtitle_view` (files with embedded subtitles),
    `track_view` (video files with more than one audio track) and `unresolved` (files with a
    problem or an unresolved job), narrowed by the request's `filters` (job languages, a folder
    compared with the path-flavour rule), plus totals over all items in review. Each probed file
    lists, per job shown, the pre-selection's `suggestions` (the track a transcription uses, the
    text subtitle tagged with the job's language), so a frontend can offer them again (the
    GUI's "Transcribe all shown" and "Extract all shown"); a response field only, no stored
    payload changes.
    `resolve_review` applies its choices all or none (`choice_invalid` with the choice's index
    and a reason: unknown item or job, item not in review, `upload_started` (in an `uploading`
    batch, an item with `audio_objects` or `extracted_subtitles` rows: only items sent back to
    review, whose rows are gone, or items not uploaded at all can change), not probed, track or
    stream out of range, a track for an audio file, a picture subtitle to extract) and returns
    the unfiltered views.
  - **Leaving the upload.** The database forgets an item's upload before R2 does, so no
    interruption leaves a manifest of this database that lists deleted audio. `exclude_items` (batch in
    `reviewing` or `uploading`, never the last item: `last_item`; after these checks, without
    `confirm` it answers `needs_confirmation` with the item count and whether audio recorded as
    uploaded for the items is deleted, and changes nothing) opens the object store first
    when the batch is `uploading` (a credentials error changes nothing; a batch in review needs
    no credentials), then in one transaction records a pending withdrawal (if any) and deletes the excluded
    items with their `audio_objects` and `extracted_subtitles` rows, so the exclusion itself
    cannot be lost. Then, per item, it aborts the open multipart uploads under the item's
    audio prefix and deletes every object there (the listing also finds objects that were
    uploaded but not recorded). Objects an interruption leaves behind belong to no item: the
    batch's cloud cleanup, or the orphan sweep at the start of the next `run_upload` (M5.10),
    removes them. `deleted_objects` counts the objects the request found and deleted.
    `batches/rereview.py` `return_item_to_review` is the upload's way to send an item whose
    source changed back to review (caller holds the batch lock; the item must belong to the
    batch): one transaction sets the item `reviewing`, deletes its audio and
    extracted-subtitle rows and sets its jobs pending; then its open
    multipart uploads are aborted and its objects deleted. Objects an interruption leaves
    behind stay under the item's prefix, where a later exclusion, re-upload (M5.10) or
    cleanup removes them. A batch with only `extract` jobs left shows `extract_only` in its
    summary; the upload takes it to `transcribed` without a manifest.
  - **Listing.** `list_batches` lists newest first, hidden batches only with `include_hidden`;
    `set_batch_hidden` hides only `downloaded` or `cleaned` batches and shows any batch again.
    `delete_batch` removes the rows of a batch in `composing` or `reviewing` (cascade; without
    `confirm` it answers `needs_confirmation` with the item count and changes nothing). Every job
    in `get_batch` (and the other batch details) shows its `status` and, for a failed job, its
    `result_error` (the result's error, `unreadable result` or `missing result`): the "failed: …"
    reason of the job view (requirements "Failure handling").
  - **Allowed actions** (`batches/actions.py`, table `ACTION_RULES` in `batches/constants.py`).
    Every `BatchSummary` lists in `actions` the batch requests that the batch allows now and
    that would change something, so frontends build their buttons from the list alone, without
    reading state fields. Per action the table names:
    - the phases its handler accepts and the guard conditions on the batch (more than one item
      for `exclude_items`; every item `reviewed` for `run_upload` from `reviewing`; no pending
      result for `cleanup_audio`; a failed or no-speech job and audio kept for
      `create_retry_batch`; no retry batch still copying for both cleanups);
    - no-op conditions, leaving out what the handler accepts but that would change nothing:
      `start_review` once in `reviewing`; `review_batch` and `resolve_review` with no item in
      review; `hide_batch` on a hidden batch and `show_batch` on a shown one; `cleanup_audio`
      after the audio is gone unless that cleanup is pending; `cleanup_everything` on a
      `cleaned` batch unless that cleanup is pending; the downloads on a `cleaned` batch without
      extracted subtitles. The handlers keep accepting these (re-sending stays harmless).

    `cleanup_cloud` is listed per scope (`cleanup_audio`, `cleanup_everything`),
    `set_batch_hidden` per direction (`hide_batch`, `show_batch`); a pending cleanup is repeated
    with the action of its scope, which is listed while `cleanup_pending` is set. The table
    restates the handlers' guards, which stay where they are. A drift test sends every action to
    seeded batches in every phase and state and checks each outcome against a closed list: a
    listed action, and an unlisted one the table leaves out only as a no-op, must give an
    outcome expected after its guard (a success, a confirmation, missing credentials, ...); any
    other unlisted action must be refused by a guard. One difference is kept and tested: a
    retry whose job set already has its retry batch returns that batch even after the source's
    audio was deleted (it creates nothing, so `create_retry_batch` is not listed then).
    Conditions outside the batch (a run active, a lock busy, the cloud unreachable, the
    request's own fields) are not in the table: a listed action can still be refused. A
    frontend then shows the error and reloads the batch; after a request that changes other
    batches too (a retry, a launch, a refresh), it reloads the list.
  - **Stage and labels** (same module, from the same facts; computed in every summary, not
    stored). `BatchSummary.stage` is the step of the guided flow (§3.1 "GUI"): `files`
    (`composing`), `review_upload` (`reviewing`, `uploading`), `transcription` (`ready`),
    `download` (`transcribed`), `done` (`downloaded`, `cleaned`, and a batch removed from the
    cloud by another computer; table `PHASE_STAGES`). `BatchSummary.labels` lists, in this
    order, the notes that apply: `cleanup_pending` (an interrupted cleanup), `upload_unfinished`
    (`uploading`), `waits_for_current_run` (§3.9 "Run scope"), `removed_from_cloud` (§3.9),
    `subtitles_ready` (`ready` or `transcribed`, not removed, with a job `ok` or `extracted`
    that has no recorded placement outcome), `flagged_jobs` (a transcription failed or found no
    speech), `audio_deleted` (until `cleaned`), `adopted` (another computer's batch, §3.9),
    `hidden`.
    Frontends show both from the catalog; a new label is a code in `BatchLabel` and its entry,
    nothing else. Each `JobView` says whether it is `flagged` (a `transcribe` job `failed` or
    `no_speech`, which a retry batch can take), so no frontend restates that rule.
- **Manifest step** (`transcribe_ui/upload/`: `manifest.py`, `publish.py`; no request of its
  own: `run_upload` and `create_retry_batch` call `manifest_step`). The manifest is published
  as soon as the upload is verified, without an activity check: a run processes only the
  batches its lease lists (A21, §3.9 "Run scope").
  - **Manifest from the database.** `build_manifest` lists every item with `transcribe` jobs,
    in selection order, with the audio objects its jobs use (by key) and its jobs by language;
    `scan_root` and `path_flavor` are the batch's (the flavour of the OS that scanned it). It is
    built only for a complete upload: every item `uploaded` and every audio object a job uses
    has a `verified` `audio_objects` row, whose payload (`upload/payloads.py`) holds the
    manifest's audio entry (track, extension, codec, duration, size); otherwise
    `upload_incomplete` (details: the item ids). A batch without `transcribe` jobs gets
    `nothing_to_transcribe` (the extract-only path). `created_at` is the batch's creation time
    and `uploaded_by` this installation's id (§3.9 "Downloading twice"), so the same database
    state always gives the same bytes and a manifest written again after an interruption is the
    same object.
  - **`manifest_step`** (caller holds the batch lock and not the runs lock, else
    `LockOrderError`): checks that the batch is `uploading` and builds the manifest; a pending
    withdrawal of a leftover manifest is finished first, under the runs lock (taken through its
    bounded wait: `runs_busy` with nothing changed) and only while no live lease lists the batch
    (else `run_active`); then the manifest is written to `batch_metadata/<id>.json` and the batch
    moves `uploading` → `ready` by compare-and-set. A failed write (an `r2_*` error) leaves the
    batch `uploading`; re-sending resumes.
  - **Order and interruption.** The manifest object is written before the phase change. A crash
    in between leaves a complete manifest for an `uploading` batch (the worker may process it,
    which is correct); the next step writes the same bytes and moves the batch.
  - **Withdrawing a leftover manifest** (`upload/withdraw.py`, `upload/leftover.py`,
    `upload/steps.py`). Exclusion and re-review change the batch such a manifest describes, so
    they withdraw it first:
    - **Ours only.** The object at `batch_metadata/<id>.json` is ours when its bytes have the
      SHA-256 a pending withdrawal recorded, or when it reads as a manifest with the batch's
      own `created_at` and `scan_root` (the batch as this database built it, from any state). Anything
      else, such as another computer's batch with the same id (another creation time), is left
      alone with its `_done.json` and results.
    - **No run lists it.** With a leftover manifest of ours, the request takes the runs lock
      through the bounded wait (it holds the batch lock; the guard refuses to run with the runs
      lock already held) and checks the live leases before changing anything (R2 alone, §3.9
      "Run scope"): a lease that lists the batch gives `run_active` (details: the run ids and
      the lease), an R2 failure its `r2_*` error, a long wait `runs_busy`, and nothing changes.
    - **Recorded, then carried out.** The change's own transaction records a
      `PendingWithdrawal` (the digest of the object found and the affected item ids,
      accumulated over changes) in the batch's `pending_withdrawal` column. Then, still under
      the runs lock: the results of the affected items (the excluded ones, or the one sent back
      to review) are deleted, then `_done.json`, then the manifest, and the record is cleared.
      Results of the other items stay, so the worker redoes only the jobs whose result is gone;
      `_done.json` must go, or the worker would skip the batch once it is published again.
    - **Who finishes a pending withdrawal.** An R2 failure after the change was saved does not
      fail the request: it is logged and the response says `withdrawal_pending: true`. The
      record is then finished, always under the runs lock while no live lease lists the batch
      and before anything else is written for the batch, by: the next exclusion or re-review of
      the batch; its `manifest_step`, before the new manifest is written; the start of every
      `run_upload` (before its orphan sweep); and the extract-only transition to `transcribed`
      (below). The launch scope counts a leftover whose digest a pending withdrawal recorded as
      absent, so no lease of this computer lists it.
    - **Remaining window.** This app cannot launch during a withdrawal: its launch holds the
      runs lock from its scope through its lease, and the scope leaves the leftover out once
      the withdrawal is recorded. Another computer's launch may read the leftover in the
      seconds between this check and the manifest delete (its lease then lists the batch); its
      worker could still write `_done.json` or a result after their deletion, and the batch
      would be skipped or keep a stale subtitle until its results are deleted again. This needs a
      crash in the manifest step, a later exclusion or re-review, and a launch on another
      computer at that moment, and is accepted.

- **Upload** (`transcribe_ui/upload/`: `services.py` (`run_upload`), `start.py`, `sweep.py`,
  `items.py`, `tracks.py`, `transfer.py`, `rows.py`, `temp.py`, `finish.py`; request
  `run_upload`, opens the database). Under the batch's lock (never waited for); R2 is opened
  once with the R2 credentials (no Runpod key is needed), so a credentials error changes
  nothing. No R2 or ffmpeg call runs inside a transaction,
  and the runs lock is taken only through its bounded wait.
  - **Start.** A batch in `reviewing` needs every item `reviewed` (else `review_incomplete`,
    details: the item ids). Batch ids are unique only in this database, so before the first
    upload `batch_metadata/<id>.json`, every object under `<id>/` and every open multipart
    upload there (`ObjectStore.count_uploads`; an error fails closed) must be absent (else
    `batch_id_taken`: another computer's batch of the same second, or one of a deleted
    database; nothing changes, the user deletes the batch and creates it again, which gives a
    new id); then the batch moves `reviewing` → `uploading` by compare-and-set. From then on
    objects under the prefix are taken as the batch's own. Remaining window: another computer
    creating the same id in the same second and checking before this one has started an upload
    shares the prefix; the sweep and the stale-upload abort below could then remove its
    objects. Accepted (two computers, same second, both uploading at once).
  - **Leftovers of an interruption.** Each batch has its own temp folder
    (`<cache>/upload/<batch_id>/`); `run_upload` empties it first (finished and partial files
    a kill left), then aborts every open multipart upload under `<id>/`. The orphan sweep
    lists `<id>/audio/` and deletes every object that is not a verified `audio_objects` row of
    the batch: objects of excluded or re-reviewed items whose deletion was interrupted, objects
    uploaded but never recorded, another extension of a track (one rule covers the plan's
    "orphans" and "what is left under a re-uploaded item's prefix"). A pending withdrawal is
    finished first, whether or not there are orphans (under the runs lock, while no live lease lists the batch),
    since its leftover manifest may list those objects; while a live lease lists the batch or
    the runs lock stays busy, the withdrawal and the sweep are put off (`sweep_skipped: true`)
    and the upload goes on: the objects are in no manifest this
    database builds, and the withdrawal is finished before any new manifest is written.
  - **Per item** (resolved items in selection order; `uploaded` ones are skipped, items in
    review are reported), each step in its own transaction, so a resume continues after the
    last recorded step:
    - the source-unchanged check (size and mtime as probed; a missing file counts as changed),
      before the item and again after every read of the file before its outcome is recorded;
      a changed file sends the item back to review (`return_item_to_review`: its rows and
      objects go). A file that cannot be stat-ed or read for another reason (an unreachable
      network share) is not a change: the request fails with `source_unreadable` (details: the
      item id) and re-sending resumes; a converted temp file that cannot be read gives
      `media_output_unwritable`. An object that does not verify is also checked against the
      source first. Media tool errors during the upload carry the item id in their details;
    - each `extract` job without a stored SRT: extracted, stored in `extracted_subtitles`
      (payload: the stream index), the job status becomes `extracted`;
    - each audio track a `transcribe` job uses without a verified object: an audio file is
      uploaded as it is (extension lower-cased; one the R2 layout's rule `[a-z0-9]{1,10}` does
      not allow, or an empty file, gives `audio_unusable`; codec: the probed first track's); a
      video track is converted into the temp folder (`<item>.a<track>`, `.mka` or `.flac`;
      codec: the track's, or `flac` after the fallback) and the temp file is deleted after the
      upload; duration: the probed container duration (0 when unknown). The target key is
      deleted first, so a HEAD after an ambiguous failure (a retried complete that gets
      `NoSuchUpload`, a lost response) can only see this request's object: such a failure
      counts as success when HEAD finds the object with the local size. The object is verified
      by HEAD size (`upload_unverified` otherwise) and recorded as a `verified` row;
    - the item moves to `uploaded`.
  - **Copy variant** (retry batches, M5.14): an item whose payload has `copy_sources` (the
    source batch's key and its `AudioObjectPayload` per track) copies each object server side
    (`CopyObject`) to `<id>/audio/<item>[.a<track>].<ext>`, verifies the size and records the
    source's payload; it has no source-unchanged check, since its audio is never read from the
    file. `CopySource.source_key` must be the layout's audio key of its track and extension
    (validated when the payload is built). A missing source object gives `r2_object_missing`;
    a track without a copy source gives `copy_source_missing` (details: item id, track).
  - **End.** Items still in review: outcome `items_in_review` (their ids), no manifest. A batch
    with `transcribe` jobs goes through the manifest step (outcome `ready`). A batch with only
    `extract` jobs moves `uploading` → `transcribed` without a manifest; a pending withdrawal is
    finished first under the runs lock, and while a live lease lists the batch it is refused
    with `run_active` (nothing more changes; re-sending resumes).
  - **Response**: the batch summary, the outcome, the items in review, the objects uploaded or
    copied and the subtitles extracted by this request, the objects the sweep deleted,
    and `sweep_skipped`. Progress: `upload_bytes` per part
    (bytes of the current object) and `upload_item` per item. `run_upload` on a batch past
    `uploading` is `wrong_phase`.
  - **Interruptions.** A kill or a cancel at any point leaves recorded steps only for objects
    that were verified; re-sending empties the temp folder, aborts open multipart uploads,
    sweeps unrecorded objects and continues, so the bucket ends with exactly the objects of an
    uninterrupted upload (tested by interrupting before and after every R2 and ffmpeg call).

- **Retry and cleanup** (`transcribe_ui/cleanup/`: `services.py` (the two handlers),
  `origins.py`, `planning.py`, `rows.py`, `retry.py`, `targets.py`, `known.py`, `unknown.py`,
  `manifests.py`, `stages.py`; requests `create_retry_batch` and `cleanup_cloud`, both open the
  database). No R2 or Runpod call runs inside a transaction.
  - **Retry request.** `create_retry_batch` names the source batch and its jobs (item id and
    language, at least one, each once). Under the source's batch lock (never waited for, so a
    cleanup of the source cannot run meanwhile) it looks for the retry batch of this source and
    job set: the batch payload's `retry_of` holds the source id and the sorted jobs, found with
    `json_extract`. A retry batch in `cleaned` no longer counts (its jobs can be retried
    again). An existing one past `uploading` is returned as it is (`created: false`, outcome
    `past_upload`, nothing done).
  - **New retry batch.** The source must not have `audio_deleted` (`audio_deleted`), must be
    `transcribed` or `downloaded` (`wrong_phase`), and every job must be a `transcribe` job of
    the source (`retry_job_invalid`, reason `no_such_job`) with status `failed` or `no_speech`
    (reason `not_flagged`; details: the job's index); each job's track needs a verified audio
    object of the source (`copy_source_missing`). The batch gets the source's scan root and
    path flavour, the jobs' languages and `retry_of`; each source item with chosen jobs becomes
    a new item (new ULID, so its own audio key) in phase `reviewed`, its payload copied with
    `copy_sources` set (the source's object key and `AudioObjectPayload` per track), its jobs
    `careful` on the same track, pending. Its id is the next free batch id of the clock (as
    `create_batch`) that R2 holds nothing under (`upload/start.py` `id_in_use`: the start check
    of `run_upload`, which never runs for a batch created in `uploading`); an id taken in R2 is
    skipped for the next one (up to 10, then `batch_id_taken`). The batch is committed in
    `uploading` before anything is copied.
  - **Copy and publish.** Under the retry batch's lock the handler reads its phase again: one
    that moved on meanwhile gives `past_upload`, one cleaned up meanwhile `wrong_phase`
    (nothing is copied into a cleaned prefix). Otherwise it runs `run_upload`'s resumable path (`upload/services.py` `continue_upload`: temp folder, stale multipart
    uploads, orphan sweep, the copy variant per item, then the manifest step). The response carries the retry batch's summary, `created`, the
    outcome (`ready`) and the objects copied. A kill or a cancel at any point is resumed by repeating the
    request (it finds the batch by `retry_of`) or by `run_upload` on the retry batch; no second
    retry batch is made.
  - **Cleanup order of locks.** `cleanup_cloud` takes the runs lock (bounded wait), then the
    batch lock of the id (never waited for). For a known batch the runs lock is held through
    the checks, the lease check and the transaction that records the cleanup, then released
    before the deletes: the recorded marker keeps every launch out, and the batch lock and the
    phase keep the manifest step out, so a long delete never
    blocks `refresh_status` (the deadline stop) or `cancel_worker`. The batch lock is held to
    the end. An unknown batch keeps both locks to the end (nothing local blocks a launch).
  - **Known batch.** Phases: `everything` from `uploading` on, again in `cleaned` (to finish an
    interrupted cleanup); `audio` in `ready`, `transcribed` and `downloaded`, refused while a
    transcribe job is `pending` (`results_pending`, details: the count). Both are refused while
    a retry batch of the batch is `uploading` with an item not `uploaded` (`retry_copying`,
    details: the retry batch). Neither is possible for a batch another computer removed from
    the cloud (`batch_removed_from_cloud`), and `audio` not for an adopted batch
    (`adopted_batch`; §3.9). Confirmations: `confirm` (the plain or the stronger form of
    "delete everything", §3.9 "Downloading twice"), and for `everything` on a `ready` or
    `transcribed` batch also `confirm_not_downloaded` while a transcribe job that did not fail
    has no recorded placement outcome (the rule that moves a batch to `downloaded`, §3.5;
    a `downloaded` batch never asks; both listed at once). The per-batch lease check (§3.9 "Run scope") refuses while a live lease lists the batch
    (`run_active`). Then one transaction records the
    intent: `audio_deleted` for both scopes (no retry can start from audio being deleted), the
    batch payload's `cleanup_pending` (the scope; shown in `BatchSummary`), and for
    `everything` the phase `cleaned` with `pending_withdrawal` cleared
    (nothing publishes the batch again). A last transaction after the last delete clears
    `cleanup_pending`. While any batch has it (a kill or an R2 failure in between: its manifest
    or audio may still be in R2), `plan_launch` and `launch_worker` refuse with
    `cleanup_pending` (details: the batch ids), so nothing half deleted is transcribed;
    repeating the cleanup clears it. The rows stay (history; extracted subtitles stay
    downloadable).
  - **Deletes** (`stages.py`; each object checked before its delete, so what is gone is not
    counted; the cancel token before each delete; progress `delete_objects` per object). Known
    `everything`: abort multipart uploads under `<id>/`, the manifest, `_done.json`, the results,
    then everything left under `<id>/`; the batch's temp folder is emptied. `audio`: abort the
    uploads under `<id>/audio/`, delete its objects. Repeating an interrupted cleanup finishes
    it.
  - **Unknown batch** (Q28): only `everything` (`audio` gives `batch_not_found`). The id is a
    `BatchId` (else `invalid_request`); `batch_metadata/<id>.json` must exist
    (`batch_not_found`) and read through `reading.read_manifest` (`manifest_unreadable`) before
    anything is deleted; one confirmation (details: the file count, `known: false`); refused
    while a live lease lists it. The manifest goes first (no launch can take the batch
    meanwhile), then everything under `<id>/` but `_done.json`, then `_done.json`. Nothing local
    remembers this cleanup, so `_done.json` without a manifest (only a worker writes it, for a
    published batch) is the proof that an interrupted cleanup left the rest: repeating the
    request asks `cleanup_unknown_leftover` (details: `batch_id`, `known: false`) and deletes
    what the listing still shows. Without either (perhaps another computer's upload in
    progress) it is `batch_not_found`, so an untranscribed batch interrupted between its
    manifest delete and its last audio delete leaves that audio (no worker can take it).
  - **Another batch under a known id** (the known rule, §3.9 "Known batches"): the cleanup of
    the known batch, after its own confirmations, is diverted to the unknown path, which asks
    its own question on the request field `confirm_unknown` (`cleanup_unknown_batch` or its
    stronger form); the known batch's confirmations never cover it.
  - **Response**: the batch id, the scope, the batch summary (None for an unknown batch), the
    objects deleted and the multipart uploads aborted by this request.

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
- ffmpeg/ffprobe: bundled static LGPL builds inside each executable (GUI and CLI; the CLI needs
  them for `run_upload`). Resolution order: the
  path set in the settings, then the bundled build (`sys._MEIPASS` of a PyInstaller build),
  then `PATH` (development runs without a bundle), so a `PATH` ffmpeg of unknown version never
  silently replaces the tested one. A settings path that is not absolute on this computer or
  not a file is an error (`media_tool_not_found`, source `settings`), never skipped or looked
  up on `PATH`. The `PATH` search is the app's own: only absolute
  entries, never the current folder (Windows' `shutil.which` searches it first), `PATHEXT` on
  Windows. The tools are resolved when first used, so requests that need no media work without
  them. The composition root (`api/wiring.py`) gives `ToolLocator` a `load_overrides` that
  reads the two path fields of the settings file at every lookup (`SettingsStore.tool_paths`;
  other fields are not checked): a changed path applies to the next tool call, and a broken
  file or path field gives `settings_invalid` only to requests that run a tool (the warning is
  logged once per process).
- Media adapter (`transcribe_ui/media/`): the `MediaTools` port (`probe`, `convert`,
  `extract`), its ffmpeg adapter and a deterministic fake for golden flows. Processes run
  through an injectable `ProcessRunner`:
  - every ffmpeg/ffprobe process starts in its own process group, so the terminal's Ctrl+C
    reaches only the app; the runner polls the cancel token and the timeout every 0.1 s and
    kills the process itself, also when anything else interrupts the wait (a second Ctrl+C),
    together with everything it started (a package-manager shim such as Chocolatey's
    `ffmpeg.exe` runs the real ffmpeg as its own child):
    - POSIX: a new session (`start_new_session`; no `preexec_fn`, which is not thread-safe);
      the kill is `killpg(SIGKILL)`. The runner tracks every running tool, so the GUI's "Quit
      now" kills them before `os._exit` (`kill_running_tools`). A tool killed that way, or one
      that ends while the cancel token is set, gives `cancelled`, never a result (so no
      `media_tool_failed` is stored). If the app itself dies otherwise, a running ffmpeg is
      orphaned and runs to its end; this is accepted, since it writes only its own uniquely
      named partial file, which the next `run_upload` of its batch removes when it empties the
      batch's temp folder (§3.3 "Upload");
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
into place, so an interrupted download never leaves a partial file.

Planner (`placement/`: `planner.py`, `custom.py`, `drafts.py`, `naming.py`, `probe.py`; the
download requests below wire it to the batches):

- Pure: `plan_placement(jobs, scan_root, options, rules, probe)` and
  `import_custom_plan(document, jobs, options, rules, probe)` (with `export_custom_plan` and
  `folder_levels`; `rules` is the target's `PathRules`, or a `TargetNames` that also carries the
  batch's own `path_flavor` in which the sources and the scan root are split, §3.9) return a `PlacementPlan` (one
  entry per job: source, target, outcome, rename reasons; and plan errors). The input is the
  planner's own models (item id, language, source path, subtitle state `available`, `empty`,
  `failed` or `pending`), not the batch model. The only I/O is the injected
  `PlacementProbe` (original folder exists, what is at a target, link resolution;
  `OsPlacementProbe` in production); the target-OS rules are the injected path-flavour rule
  (§3.2), so the Windows rules are tested on Linux. A plan with errors is never carried out.
- Strategy names: `next_to_media`, `mirror`, `flat`, `flat_with_folders` (the same names as
  the placement settings). `flat_with_folders` takes `folder_count` (X) or explicit
  `folder_levels` counted from the scan root (1 = the first folder below it; levels a file
  does not have are skipped), not both (levels with a `folder_count` other than 1 are
  refused). `mirror` and both flat strategies need an absolute `output_folder`
  (`output_not_absolute`); `next_to_media` ignores one, so settings pre-filled with an output
  folder still apply. The scan root and every source path must be absolute without `..`
  parts (`scan_root_invalid`, `source_path_invalid`), and every part of a source below the scan
  root a single name in the batch's flavour (not empty, `.` or `..`, no separator, drive or
  anchor: `source_path_invalid`), so no target leaves its folder. Another computer's batch
  cannot be placed next to its media (`no_local_sources`, §3.9). A
  source outside the scan root is `outside_scan_root` for `mirror` and `flat_with_folders`
  (`flat` places it without folder names).
- Names are worked out for every job, whatever its subtitle state, so a partial download (requirements "Subtitle download options")
  names files as a later one does. Collisions compare under the path-flavour rule
  (case-insensitive on Windows and macOS). In both flat strategies the colliding files get the
  names of their closest folders added (on top of the strategy's chosen ones), one level at a
  time until files of different folders differ; files that still collide (same folder) all get
  their source extension (`movie_mkv.hu.srt` and `movie_mp4.hu.srt`, also next to the media and
  in `mirror`). The steps repeat in rounds (at most 64) while a new name meets another file's
  (`movie_mkv.hu.srt` and a source `movie_mkv.avi`; `A_movie.hu.srt` and a root file
  `A_movie.mp4`). The entry lists the reasons (`folders`, `source_extension`). What still
  collides when no step changes a name is `duplicate_target` for every job on that target,
  whatever its outcome (names are fixed per job, so the collision is real before both
  subtitles exist). By then each name holds all its folders below the scan root, its stem and
  its extension, so this needs a `_` inside one of those names (from the source or a replaced
  character): `A_B/m.mkv` and `A/B/m.mkv`, or a root `A_movie.mkv` and `A/movie.mkv`.
- Generated names are made valid: forbidden characters (Windows `<>:"/\|?*` and control
  characters; POSIX `/` and NUL) become `_`; on Windows trailing dots and spaces become `_`
  and a reserved device name (`CON`, `PRN`, `AUX`, `NUL`, `COM0`-`COM9`, `LPT0`-`LPT9`, the
  superscript-digit forms, `CONIN$`, `CONOUT$`; also with an extension) gets `_` after its base
  (`CON_.hu.srt`). Name length is counted as the file system does: UTF-8 bytes on POSIX, UTF-16
  units on Windows. The limits leave room for the tail `.<token>.tmp` (`TEMP_TAIL_LENGTH`, 11
  ASCII characters) of the temp file the subtitle is first written to (`run_download`): a name
  over 255 with it is `name_too_long`; on Windows a temp file path over 259
  UTF-16 units (MAX_PATH) or a target folder path over 247 (the 248 that folder creation
  allows, minus the NUL) is `path_too_long`, since long paths may fail on a system without
  long-path support. These name errors are reported for every job, whatever its outcome.
- Outcomes: `pending` → `not_finished` (requirements "Subtitle download options"), `failed` → `skip_failed`, `empty` →
  `skip_empty` unless `include_empty`; next to the media a missing original folder gives
  `skip_folder_missing` for that file while the others are placed (requirements "Subtitle download options"); an
  existing file → `skip_existing`, or `overwrite` when asked; a folder in the way is skipped,
  and asking to overwrite it is `target_not_a_file`; a target that cannot be checked (no
  permission) is `target_unreadable`, never a skip. These target errors are reported only for
  entries that would write. `PlanError.code` is a `Literal` of every code. Error messages name files by file name and language only (no folders), so
  golden files need no normalisation of messages.
- Custom plan: the export (`CustomPlan`, `version` 1) lists every job with `item_id`,
  `language`, `source_path`, `original_folder`, and `output_folder` (relative to the output
  folder chosen at import; pre-filled with the `mirror` folders) and `output_file_name`
  (pre-filled with the default name). A source `mirror` cannot place (outside the scan root,
  or not absolute without `..`) gets an empty `output_folder` (the output folder itself) for
  the user to fill in. Import errors, each with a message naming what is wrong:
  `custom_plan_schema` (invalid JSON, unknown fields, wrong version; locations and messages
  only, never input values), `custom_plan_duplicate_job`, `custom_plan_unknown_job`,
  `custom_plan_missing_job`, `custom_plan_source_changed` (`source_path` or
  `original_folder` edited), `custom_plan_invalid_name` (each folder part and the file name
  checked, not repaired: forbidden characters, `name:stream`, trailing dot or space, reserved
  names, length), `custom_plan_not_srt`, `custom_plan_outside_root` (the target, after
  resolving `..` lexically and links through the probe, must be below the resolved output
  folder) and `duplicate_target` (compared after resolving). Jobs are keyed by item id and
  language.

Download (`placement/`: `services.py`, `sources.py`, `download.py`, `writer.py`, `recording.py`;
requests `plan_download`, `run_download`, `export_custom_plan`, `import_custom_plan`; all open the
database):

- **Phases.** Allowed in `ready` (the jobs whose results the last `refresh_status` recorded; the
  others are `not_finished`, requirements "Subtitle download options"), `transcribed`, `downloaded` (again, any number of times, with
  any strategy, requirements "Subtitle download options") and `cleaned` (only the extract jobs are planned: their SRT is in SQLite, the
  transcribed results are gone); any other phase is `wrong_phase`. A job's subtitle state comes
  from the database: a transcribe job's status (`ok` → `available`, `no_speech` → `empty`,
  `failed` → `failed`, `pending` → `pending`), an extract job's stored SRT. Sources are the items'
  paths, the scan root the batch's, the name rules this computer's (`Ports.path_rules`), and the
  planner asks the real file system (`OsPlacementProbe`).
- **Requests.** `plan_download` (batch, `placement`: the strategy options with their defaults)
  returns the batch summary, the plan and the batch's folder levels below its scan root
  (`folder_levels`: each level with up to three of its folder names, sorted, and how many it
  has; `placement/levels.py`, split like the planner splits, names equal under the target's
  rule counted once), so a frontend offers `folder_levels` without splitting paths, and
  writes nothing. It and a `check_only` import check the cancel token before each file system
  question (`CancelCheckProbe`), so a plan replaced by a newer one stops early. `run_download` (the same, plus
  `confirm_overwrite`) carries it out. `export_custom_plan` returns the batch's custom plan and,
  with an absolute `path`, also writes it there as JSON (through a temp file, below;
  `custom_plan_unwritable` with the exception type). `import_custom_plan` reads the edited plan
  from an absolute `plan_path` (at most 32 MiB; `custom_plan_unreadable` with the exception
  type), validates it against the batch's jobs and `placement` (output folder, `overwrite`,
  `include_empty`) and carries it out; with `check_only` it only returns the validated plan, errors
  included. Plan, export and a checked import take no lock; `run_download` and
  `import_custom_plan` hold the batch lock (never waited for).
- **Carrying out** (`download.py`). A plan with errors is refused as `download_plan_invalid`
  (details: the plan errors) before anything is written. A plan that would overwrite files is
  answered with `needs_confirmation` (`confirm_overwrite`; details: the target file names) and
  nothing changes. Then job by job, in plan order, with the cancel token between jobs: the target
  is checked again first, before anything is read (next to the media the folder must still exist
  and is never created: else `skip_folder_missing`; a target that appeared since the plan is
  `skip_existing` unless overwriting); then the subtitle is read (an extracted one from SQLite, a
  transcribed one from `<batch>/results/<item>.<lang>.srt` in R2, opened on first use so a
  download of extracted subtitles needs no R2 credentials, an empty result as an empty file),
  written into place (`writer.py`, below) and the job's outcome (the planned skip, or what was
  done) recorded in its own transaction as a `placements` row (`not_finished` is no outcome).
  No R2 call and no file write happens inside a transaction.
- **Per-job failures.** A job that cannot be placed does not stop the others: a finished job
  whose SRT is gone from R2 (`subtitle_missing`) or a write the OS refuses
  (`download_write_failed`, with the exception type as `reason`: a target held open on Windows, a
  read-only folder, a link planted at the temp name) gets no outcome row and is listed in the
  response's `failures` (item, language, target, code, message naming the file by name only); the
  request still succeeds, and sending it again tries those jobs again. Failures that concern every
  job (R2 or its credentials, the database) fail the request. The response lists the jobs carried
  out with the outcome as carried out, the failures, and counts the files written, the skips and
  the jobs not finished; progress: one `download` event per job with its target.
- **Writing a file** (`writer.py`). The bytes go to a new temp file next to the target,
  `<name>.<token>.tmp` with 6 random hex characters, opened with `O_CREAT | O_EXCL` plus
  `O_NOFOLLOW` (POSIX) and `O_BINARY` (Windows), so a planted link or hard link at that name is
  never written through (the job fails instead) and two downloads of one target never share a
  temp file; it is flushed to disk. A new file is moved without replacing anything: a hard link
  plus removing the temp file on POSIX, `os.rename` on Windows; a target created in between makes
  the move fail and the job `skip_existing`. On a file system without hard links the target is
  checked and replaced (a target created in that moment is replaced: accepted). An overwrite uses
  `os.replace` (which replaces an existing file on Windows too). On Windows a move refused for a
  sharing violation is retried for about 2 s (`state/files.py` `with_sharing_retries`). On POSIX
  the folder is synced after the move (`sync_folder`). A failed write removes its temp file.
  `export_custom_plan` writes its file the same way, replacing an existing one.
- **Interruptions.** A cancel, a failure or a kill keeps the outcomes recorded so far. Sending the
  request again plans anew: files placed before are existing targets (skipped unless overwriting,
  so their jobs get their outcome then). A temp file a hard kill left behind is removed by the
  next write of the same target once it is 10 minutes old (`STALE_TEMP_S`; a younger one may be
  another download's).
- **Custom plan files.** `import_custom_plan` opens `plan_path` without waiting (`O_NONBLOCK`) and
  reads it only when it is a regular file (else `custom_plan_unreadable`, reason `not_a_file`: a
  FIFO would block under the batch lock), at most 32 MiB (reason `too_large`); a leading UTF-8
  byte order mark is ignored. With `check_only` it takes no lock, like `plan_download`.
- **The `downloaded` rule** (`recording.py`). A batch moves `transcribed` → `downloaded` (compare
  and set) when every job has a recorded outcome (placed, overwritten, or skipped as an existing
  file, a missing folder, an empty or a failed result) or its result failed. A failed job never
  blocks the batch: it can fail at `_done.json` after the last download (missing or unreadable
  result), and retry batches take failed jobs (requirements "Subtitle download options"). A batch whose
  every job failed therefore goes straight on to `downloaded`. `run_download` and
  `import_custom_plan` apply the rule after each download; `refresh_status` applies it in the
  transaction that moves a `ready` batch to `transcribed` (a batch downloaded in full while
  `ready` goes straight on), and to every `transcribed` batch whose lock is free (a download killed
  after its last outcome). Downloading again only adds rows; the phase never moves back, and
  `cleaned` stays `cleaned`.

### 3.6 Credentials

- R2 access key/secret, R2 endpoint/bucket and the Runpod API key are stored with `keyring`
  (Windows Credential Locker, macOS Keychain, Linux Secret Service) under the service
  `transcribe-ui`, one entry per credential (`r2_endpoint_url`, `r2_bucket`,
  `r2_access_key_id`, `r2_secret_access_key`, `runpod_api_key`). Non-secret settings live in a
  JSON config file in the user config dir. (`credentials/store.py`; the backend is injected:
  `keyring.get_keyring`, created on first use; tests use an in-memory one.)
- Environment fallback, per credential: the keyring entry wins when it is set; otherwise the
  environment variable of the same name as in `.env` and the worker (`R2_ENDPOINT_URL`,
  `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `RUNPOD_API_KEY`) is used. Values
  are stripped of surrounding white space; empty means unset. A keyring that is missing
  (`NoKeyringError`: headless Linux, WSL) or fails (locked, D-Bus or Windows API errors) is
  reported and skipped, so the environment still serves. Development uses an uncommitted
  `.env`. The optional pods-only terminate key (plan m5 §1 "Runpod keys") is added only if M4
  shows the pod-scoped key cannot delete its own pod.
- Requests (all DB-free):
  - `store_credentials`: any subset of the five, keys and secrets as `SecretStr`, at least
    one. Endpoint: an ASCII `https` URL with only a host (and port), stored without a trailing
    slash; bucket: R2's naming rule; keys: ASCII without white space or control characters
    (they go into HTTP headers; a stored non-ASCII value is `credentials_invalid`), at most 1024
    characters (fits the Windows Credential Manager limit). The response names what was
    stored. No keyring → `keyring_unavailable`, whose message names the environment variables
    to set instead; a backend failure → `keyring_failed`; both list what was written before.
  - `credentials_status`: the keyring state (`available`, `unavailable`, `failed`) and, per
    credential, its environment variable and source (`keyring`, `environment`, `missing`).
  - `check_credentials`: per service an outcome `ok`, `missing` or `failed` with the error
    code and message (a failed check is a success response). R2 lists one key of the bucket
    (`r2_auth_failed`, `r2_access_denied`, `r2_bucket_not_found`, `r2_unreachable`,
    `r2_failed`); Runpod reads one entry of the pod list (`runpod_key_rejected`,
    `runpod_key_missing_scope`, `cloud_unreachable`; `credentials_missing` /
    `credentials_invalid` from the store). Each check's client makes 2 attempts with a 5 s
    connect and a 10 s read timeout (the Runpod check waits at most 1 s between them, even for
    a 429 asking for more, and stops on the request's cancel token), so a hanging service is
    reported within about 30 s (an unreachable host within about 10 s); the transfer clients
    keep the worker's limits (§6), the Runpod client its own (§3.7).
  - Operations that need credentials get `credentials_missing` (names the credentials and
    their variables) or `credentials_invalid` (names the credentials and their sources) from
    the store.
- Values never leave the store except to the client that uses them: no response, error
  message, error detail, exception message, traceback or log record contains one (the
  endpoint and bucket included). A key that is only `*` characters (a masked value pasted
  back) is refused. Validator messages are fixed texts; the request union and the credential
  models set `hide_input_in_errors`, so even `str(ValidationError)` omits the input; SDK error
  texts are never passed on (§6). Regression tests feed sentinel values through every
  validator and every flow and search responses and DEBUG-level logs for them.
- The desktop's Runpod API key needs these scopes (OpenAPI `security` of each operation):
  `pods:read`, `pods:write` (pods and queued pods), `templates:read`, `templates:write`,
  `secrets:read`, `secrets:write`, `volumes:read`, `volumes:write` (only `setup_cloud` writes
  templates, secrets and volumes) and `catalog:read` (GPU price). A 403 names the missing scope
  (`runpod_key_missing_scope`); a 401 is `runpod_key_rejected`. A separate pods-only terminate
  key for the pod exists only if M4 shows the pod-scoped key cannot delete its own pod.
- The Runpod requests take the API key (and `setup_cloud` the R2 credentials) from this
  credential store (`Ports.credentials`: keyring first, then the environment). `setup_cloud`
  checks all five first, so one `credentials_missing` names every missing one.

### 3.7 Launching the worker

- Runpod **REST API v2** (`https://api.runpod.io/v2`; v1 was retired on 2026-11-15) through a
  thin typed `httpx` client (`transcribe_ui/runpod/`: `client.py` with `pods.py` and
  `account.py` per resource, `session.py` for the HTTP session, `calls.py` for the calls and
  the host check, `retry.py` for the policy, `timing.py` for waits, clock and cancel,
  `parsing.py`, and `wire.py`/`bodies.py` for the bodies read and sent). The key is sent only
  over https to exactly `api.runpod.io` (host compared case-insensitively; no port, no
  trailing dot, no other Runpod host), checked for the base URL and again for every request;
  redirects are never followed; environment proxies are ignored when a test transport is
  injected; error messages name the call and the status or exception type only (no URL,
  header or body), and errors are raised outside `except` blocks, so no exception chain
  (`__cause__` or `__context__`) keeps a request or a response. Lists follow
  `pagination.nextCursor` (at most 100 pages).
- Retries: reads, deletes and updates (PATCH is idempotent) retry a 429 after `Retry-After`
  (delta seconds or an HTTP date measured against the injected clock, clamped to 0-60 s; a
  missing, unreadable or non-finite value backs off instead), a 5xx or a network failure with
  backoff (1, 2, 4, 8 s), up to 5 attempts; other 4xx are final. A create retries only a 429
  and a failure to connect (`ConnectError`, `ConnectTimeout`, `PoolTimeout`: nothing was
  sent). A timeout, a 5xx, a redirect, a drop after sending, or a 2xx whose body does not
  parse (the object exists) raises `AmbiguousCreateError` and is never re-sent. Waits go
  through an injected wait that ends early on the request's cancel token, and no attempt
  starts once it is set (`cancelled`), so a cancel never lags behind a long `Retry-After`;
  tests record the waits instead of sleeping.
- Bodies read from the API keep only the fields the app uses: a pod's `env` (R2 secrets and
  the pod-scoped key) is never parsed, template env values are `SecretStr`, and no response,
  log line or error message contains env values, keys or R2 secrets. Templates are validated
  only after filtering by name, so an unrelated template of an unexpected shape cannot fail
  the setup.
- Cloud setup (`setup_cloud`, `cloud_setup_status`; DB-free, stateless, nothing stored
  locally): by fixed names, the Global Volume `transcribe-ui-backups`, four Runpod secrets
  holding the R2 endpoint, bucket, access key id and secret (`transcribe_ui_r2_*`, from the
  desktop's credentials: the one bucket-scoped token the desktop and the pod share, A18) and
  the template `transcribe-ui-worker-schema-<contracts major>` (image
  `markfazekasgh/transcribe-ui-worker:schema-<contracts major>`, `startSsh`/`startJupyter`
  false, env `R2_*` = `{{ RUNPOD_SECRET_transcribe_ui_r2_* }}`). The template name carries the
  major, so app versions of different contract majors keep their own template instead of
  rewriting one back and forth. `setup_cloud` creates what is missing, always rewrites the
  secret values (they are write-only, so a change cannot be detected; a token rotation is a
  re-run), and updates an outdated template (image, `env` entries the app sets, `startSsh`,
  `startJupyter`; other env entries are ignored, so merge or replace on PATCH does not
  matter). Duplicate secrets and templates are deleted: of secrets the exact-name match is
  kept before case variants (Runpod compares names case-insensitively), then the oldest
  (`createdAt`, then id); of templates (no `createdAt`, ids not ordered by age) a stable pick
  that prefers one already up to date, then the lowest id. A duplicate template still in use
  (400) stops with `cloud_object_in_use`. Global Volumes are never deleted (they may hold
  result backups): several with the app's name stop `setup_cloud` with
  `cloud_volume_duplicated`, listing their ids, and the user removes the unwanted ones in the
  Runpod console (Storage page) and runs the setup again (Q29; no other handling). An
  ambiguous create, or a 409 on a secret name the list does not show yet, stops with `cloud_setup_interrupted`; a re-run adopts the object, or (during list
  lag) creates a second one that the run after removes (for a volume: reported as duplicated).
  `cloud_setup_status` reports each object as `ok`, `missing`, `outdated` (with field names) or
  `duplicated`; `require_cloud_setup` gives `launch_worker` the template and volume ids, or
  refuses with `cloud_volume_duplicated` (the same message with the ids), `cloud_not_set_up` or
  `cloud_setup_outdated` (duplicates included), so no pod mounts an arbitrary volume.
- `runpod/check.py` is the Runpod check of `check_credentials` (§3.6; `GET /v2/pods?limit=1`,
  body not parsed, 2 attempts, 5 s connect and 10 s read timeouts).
- `POST /v2/pods` with: `gpu {id: "NVIDIA RTX A5000", count: 1, minCudaVersion: "12.8"}` (excludes
  A5000 hosts that report CUDA 12.4), `cloud: SECURE` (on-demand; spot no longer exists),
  `disk: 60`, the Global Volume mount, the template (which alone pins the image tag), non-secret
  env, and `queue: {expiresAt: now + N h}` for **deploy when available**; the user sets the
  wait at every start, 5 min to 24 h (A19), default 1 hour. A 202 response means "queued";
  the app shows the queued state and can cancel it (a queued pod that deploys during the cancel
  is terminated; the run counts as cancelled only once that is verified). Before starting, the
  app shows the total audio duration and an estimated cost range for confirmation; there is no
  in-app spending limit (the account's own limit applies). SSH uses Runpod's proxied basic SSH,
  so no port or sshd is configured.
- Pod name (`transcribe_contracts.pods`): `transcribe-ui-<run_id>-until-<YYYYMMDDTHHMMZ>`,
  the run's ULID and a UTC deadline rounded up to the whole minute (61 characters; the v2 spec
  sets no maximum, the builder keeps to 63). A queued pod carries the same name as the pod it
  deploys. The deadline is the moment after which nothing legitimate can still be running: the
  queue's `expiresAt` + `MAX_RUNTIME_S` (which includes the start-up allowance for the image
  pull and model load) + 10 min, rounded up. Every launch sends `queue`, so a pod that starts at
  once gets the same deadline; the pod may deploy just before the queue expires. The
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
- "Start transcription" launches one worker for every manifest in `batch_metadata/` without
  `_done.json`; the worker processes only the batches its lease lists (§3.9 "Run scope"), so the
  estimate and `MAX_RUNTIME_S` cover exactly them, including batches this computer does not know (listed
  separately, Q28, §3.9). The app shows:
  waiting for a machine (cancellable), starting, transcribing (batch, file, elapsed time,
  cost so far), finished or failed.
- **Run activity** (`transcribe_ui/runs/activity.py`, with `pods.py`, `heartbeats.py` and
  `leases.py`; `check_activity`): used only by `launch_worker` (cleanup and the withdrawal use
  the per-batch lease check, §3.9 "Run scope"), always under the runs lock (checked: `LockRegistry.holds_runs`,
  else `LockOrderError`). It returns every sign of a live run (`RunActivity.sources`: kind,
  name, status and the run id, so a refusal can name the run); none means inactive. A run is
  active when:
  - a local run's status is not final (final: `finished`, `failed`, `cancelled`,
    `launch_failed`; a status this version does not know counts as active);
  - the Runpod lists hold a `transcribe-ui-*` pod in any status but `TERMINATED`, or a
    `transcribe-ui-*` queued pod in any status but `FAILED`, `EXPIRED` and `CANCELLED` (an
    unknown status counts). A `DEPLOYED` one is over once its name deadline has passed (no
    lookup). Before that: if its pod is in the pod list under any name, that pod's status
    decides (an app pod counts as itself); without a `podId` it counts; otherwise its pod is
    looked up by id (`GET /pods/{id}`): live counts, `TERMINATED` does not, and a 404 counts
    until `DEPLOY_SETTLE_S` (10 min) after the deploy (`resolvedAt`; a missing or later
    `resolvedAt` keeps it counting), since a pod missing right after a deploy may be list lag.
    The plan's "404 after the pod was seen" is implemented as this settle time, which needs no
    memory of earlier sightings (a pod that ran while the app was closed was never seen). The
    lookup is made for every such entry until its deadline, so a skewed local clock or a pod
    the list leaves out cannot make a billing pod look gone;
  - a `runs/*/status.json` written (R2 `LastModified`, kept in `ObjectInfo.modified`) within
    `HEARTBEAT_STALE_S` (10 min) + `HEARTBEAT_MARGIN_S` (5 min) of the clock either cannot be
    read (invalid or another schema major: fail closed) or has a non-final state with an
    `updated_at` at most `HEARTBEAT_STALE_S` + `HEARTBEAT_MARGIN_S` before the clock (the margin
    covers a worker clock behind this one). An `updated_at` ahead of the clock counts, but is
    taken as at most the run's `deadline_at` + the margin. An older object never counts,
    readable or not, so a broken or far-future heartbeat cannot block for ever;
  - a run lease counts (`runs/<run_id>/launch.json`, kind `lease`; §3.9 "Run scope"), another
    computer's included, except one written more than `LAUNCH_SETTLE_S` ago whose run has no
    live entry in the Runpod lists (its pod was never created or is gone), so a stale lease
    does not block Start for hours.

  The check fails closed: a Runpod list or lookup that fails (after the client's retries)
  or an R2 list or read that fails raises `cloud_unreachable` (details `part`: `runpod` or
  `r2`, `cause`: the error code, e.g. `runpod_key_rejected`), never "inactive". The launch opens R2 and the Runpod
  API with all five credentials in one lookup (`runs/cloud.py` `open_cloud_client`; no call yet,
  so a credentials error changes nothing).
- **Launch scope** (`transcribe_ui/runs/scope.py`, `manifests.py`; `read_launch_scope`): what
  the next run processes, read the way the worker plans (§5.3). For each object under
  `batch_metadata/`: a key that is not a manifest key is unreadable (`not_a_manifest`); a batch
  with `_done.json` is skipped; a manifest of another major (`unsupported_schema`) or an invalid
  one (`invalid`) is unreadable and not counted; otherwise every job whose result JSON is not
  listed under `<id>/results/` is pending (with its mode and audio duration). A leftover
  manifest a pending withdrawal will delete counts as absent. Each batch says whether this database knows it (`known`; unknown ones are
  another computer's, Q28; the rule is §3.9 "Known batches") and its file count. The
  fingerprint is the SHA-256 of the sorted covered batch ids, the sorted pending jobs (`<batch>/<item>/<language>/<mode>`) and the total
  pending duration (3 decimals): a new result or another batch changes it.
- **Plan and estimate** (`runs/planning.py`, `estimate.py`; request `plan_launch`, DB, no
  lock, reads only; field `test_mode`): the launch scope, `MAX_RUNTIME_S`, the cost range and the
  wait bounds (default 1 h, 5 min to 24 h, A19). `MAX_RUNTIME_S` = 20 min start-up
  allowance + per pending job (audio × 0.2 × 2 + 60 s), `careful` jobs counted twice, capped at
  24 h; test mode a fixed 2 h. Cost = price per hour × (5 to 20 min start-up + weighted audio ×
  0.05 to 0.2), rounded to cents; in test mode the pod stays up for its whole 2 h, so that
  replaces the audio term. The price is the secure price of `NVIDIA RTX A5000` from
  `GET /v2/catalog/gpus`; any failure of that call (a key error included) or a missing entry
  gives the fallback 0.27 USD/h with a `note` (the plan is informational; the launch reports key
  errors itself). The factors are placeholders recalibrated at M7.
- **Launch** (`runs/launch.py`, `starting.py`, `creating.py`, `pod_body.py`, `records.py`,
  `payloads.py`;
  request `launch_worker`: `wait_s`, `test_mode`, `confirm_fingerprint`). Under the runs lock
  for the whole sequence, no transaction across a Runpod or R2 call (first, as in `plan_launch`,
  a batch with `cleanup_pending`, an interrupted cloud cleanup, gives `cleanup_pending`, §3.3):
  1. the activity check: any sign of a run (a straggler included) gives `run_active`, an
     unreadable cloud `cloud_unreachable`;
  2. `require_cloud_setup` (template and volume ids, or `cloud_not_set_up`,
     `cloud_setup_outdated`, `cloud_volume_duplicated`);
  3. the plan again: no pending job gives `nothing_to_launch` (before any confirmation); a
     missing or different `confirm_fingerprint` gives `needs_confirmation` (field
     `confirm_fingerprint`, details `plan`, the message saying when the plan changed), and
     nothing changes. The plan's `fingerprint` hashes the scope's fingerprint, `MAX_RUNTIME_S`
     and the test mode, so a plan confirmed without test mode cannot start a test-mode run;
  4. the run row (`launching`, run id from the id source, pod name with the deadline, payload
     `RunPayload`: runtime, test mode, wait, queue `expiresAt`, name deadline, covered batch
     ids, fingerprint, pending duration, estimate) is committed; the lease
     `runs/<run_id>/launch.json` (the covered batch ids, the name deadline) is written next (a
     failed write makes the run `launch_failed` with nothing created, §3.9 "Run scope");
  5. one `POST /v2/pods` with the body of plan m5 §1 (template id, no image; GPU with
     `minCudaVersion` 12.8; `SECURE`; 60 GB disk; the Global Volume at `/workspace`; env
     `TRANSCRIBE_RUN_ID`, `MAX_RUNTIME_S`, `KEEP_POD_ALIVE`; `queue.expiresAt` = now + wait).
     The name deadline is `expiresAt` + `MAX_RUNTIME_S` + 10 min, rounded up to the minute.
  The covered batch ids are the scope's: the run's lease lists exactly them. Outcomes (`outcome`): 201 → `pod` (run `running`
  with the pod id), 202 → `queued` (run `queued` with the queued-pod id). Runpod refusing (any
  4xx after the client's 429 retries, or no connection before sending) created nothing: the run
  becomes `launch_failed` and the answer is `launch_refused` (details `http_status`), or the key
  and reachability codes for 401, 403 and a failed connection. A cancel before the request was
  sent (only a 429 or a failed connection came before) also makes it `launch_failed`. A 2xx
  whose `status` is not one of the v2 statuses of its kind (pod statuses for 201, queued-pod
  statuses for 202) is ambiguous too. An ambiguous failure (`AmbiguousCreateError`) is never
  re-sent: both lists are read once and a pod or queued pod with the run's name is adopted
  (`adopted`: true; `outcome` `pod`, `queued`, or `ended` for a queued pod found cancelled,
  expired or failed, the run then `cancelled` or `failed`); otherwise, and when that lookup
  fails or is cancelled, the answer is a success with `outcome` `unconfirmed` and the run stays
  `launching`. `sending_at` is written before each attempt of the POST and `posted_at` after it
  ended, so a crash during the request leaves a time no earlier than the last send.
- **Resolving a `launching` run** (`runs/settling.py` `resolve_launching`; used by
  `reconcile_cloud`, `cancel_worker`, `refresh_status` and the launch's own lookup): a pod in
  the pod list with the run's name (a live one before a terminated one) is
  adopted as seen and the run becomes `running` (also when the pod already ended:
  `refresh_status` reads its end); a queued pod naming its pod gives `running` with that pod
  recorded as not seen (`pod_seen` false, `pod_deployed_at` = the queued pod's `resolvedAt`);
  a queued pod alone gives `queued` (`QUEUED`), `cancelled` (`CANCELLED`), `failed` (`EXPIRED`,
  `FAILED`) or `running` (`DEPLOYED` or an unknown status). With neither, the run becomes
  `launch_failed` only once `LAUNCH_SETTLE_S` (10 min) has passed since the latest of
  `posted_at`, `sending_at` and the row's creation (a retry after a 429 may send late), and two
  lookups after that, at least `LAUNCH_LOOKUP_GAP_S` (60 s) apart, found nothing (the first one
  is recorded as `first_empty_lookup_at`). A pod that appears after that is a straggler. A run
  that becomes final here records its end: `ended_at` and the end reason `queue_ended` (a queued
  pod found ended) or `launch_failed` (also set when Runpod refused the pod request).
- **Cancel** (`runs/cancel.py`, `stopping.py`; request `cancel_worker` with `run_id` and
  `confirm`, default false; runs lock): without `confirm` (Q34; `runs/cancel_asking.py`) the
  request reads only the database (no runs lock, no Runpod call, so the question shows at once
  even while a check holds the lock) and answers `needs_confirmation` (code `cancel_worker`,
  `set_to` true, details `run_id`, `stage`, `run_status`; its text says that finished subtitles
  are kept and the files not done yet wait for the next start, or, while `launching` or
  `waiting`, that nothing was transcribed yet); a `cancelled` run answers as done and another
  final run is refused (`run_not_active`) as below; nothing changes. Confirmed: a `launching` run is resolved first (one lookup); still unresolved gives
  `launch_unresolved`. A run with a pod id: `DELETE` the pod, then `GET` it: `TERMINATED`
  verifies it, and so does a 404 for a seen pod (a pod read showed it). A pod known only from
  its queued pod's `podId` is not seen: a 204 on its `DELETE` is no sighting (Runpod may answer
  it for an id it does not know yet; open item 7 of `research/runpod.md`), only a `GET` that
  returns the pod (any status; recorded as `pod_seen`) is. A 404 for it (on the `DELETE` or the
  `GET`) within `DEPLOY_SETTLE_S` of its deploy may be lag, so it gives `stop_unverified` and the
  run stays `running` with the pod recorded; re-sending the cancel finishes it once the pod is
  read or the deploy has settled. A run with only a
  queued-pod id: `DELETE` the queued pod, then `GET` it: a pod id in it means it deployed
  during the cancel, so the run records that pod (not seen) and becomes `running` (followed
  from then on), and the pod is terminated and verified; `CANCELLED`, `EXPIRED` or `FAILED`
  verify the cancel. A queued pod that is gone (404) after the cancel may have deployed just
  before it: the pod an earlier read of it named (`cancel_queued_pod`), else every live pod
  listed under its name (the run's pod name), is terminated and verified the same way
  (`stopping.QueuedTarget`). Every one is stopped even when one fails; the first failure is
  raised afterwards and the run follows the first pod whose stop did not show, so it stays open
  until all are verified. **Invariant: a run becomes final only while no live pod carries its
  name and no queued pod of it can still deploy.** A stop that is cut short (several failures,
  a cancel between two pods) may leave pods the run does not record, and a run that adopted a
  pod by its name may still have a waiting queued pod. So `cancel_worker` and the deadline stop
  (`halting.stop_run`) stop the run's own pod and then cancel its queued pod
  (`halting.cancel_waiting_queue`, read as the queued stop below: done once a read shows it
  ended, or the pod it names is stopped, or it is gone by the settle rule; still `QUEUED` or
  deployed without its pod's id is not verified; a pod it names is recorded on the run, which
  follows it, and then terminated, since the run's own pod is verified stopped by then), or
  stop its queued pod and what it deployed, and then every other live pod listed under the
  run's name (`stopping.stop_named_pods`, tried even when the run's own stop failed; one whose
  stop does not show is recorded, unless the run follows its own failed pod, and the first
  error is raised once all were tried). `refresh_status` makes a run whose pod is gone follow
  another live pod listed under its name, else the pod its listed queued pod names (not seen,
  unless that pod is listed `TERMINATED`; `KnownPod` then settles it), or keeps it open while
  its queued pod is listed `QUEUED`, instead of ending it; such a run is still stopped at its
  deadline, which also cancels the queued pod again. The straggler stops leave such a run open,
  keeping the pod it follows when that pod was not stopped and is not listed `TERMINATED`
  (live, or not listed yet: `refresh_status` decides when it is gone). A pod that appears
  under the name only after the last list (list lag) is caught by the next refresh while the
  run is open; if the run became final meanwhile, `reconcile_cloud` reports the pod as a
  straggler, collection waits for it (`lingering`), and the worker's own exit and the reaper
  bound it.
  With none of these pods the 404 counts as cancelled only by the settle
  rule `refresh_status` uses for a vanished queued pod (`stopping.vanish_verdict`):
  `DEPLOY_SETTLE_S` after the earlier of the queue's `expiresAt` and the cancel's
  acknowledgement (`cancel_acknowledged_at`, recorded on the run only after the DELETE was
  answered with success), and two empty lookups at least `LAUNCH_LOOKUP_GAP_S` apart (the first
  recorded in `first_empty_lookup_at`); before that it gives `stop_unverified`. A read after the
  cancel that still shows the queued pod voids an earlier empty lookup. The acknowledgement is
  kept even when that read still shows `QUEUED`: it follows the 204 in the same call (lag after
  the cancel), and voiding it could leave the run waiting for hours should Runpod then forget
  the queued pod. A later `QUEUED` read by `refresh_status` (a cancel that did not take effect)
  voids it, and that read clears an empty lookup too. A queued pod an earlier read showed
  ended without a pod needs none of this. Without a local run (a straggler of no run of this
  database), the 404 with no pod listed under its name is the final answer `cancelled`: no
  local run can become final, and a pod that shows up later is listed by `reconcile_cloud` as a
  straggler (the reaper bounds it by the name deadline). A read without `expiresAt` takes the
  local run's.
  `expiresAt` is Runpod's clock and the acknowledgement this computer's: the rule assumes they
  differ by much less than `DEPLOY_SETTLE_S`. A pod the queued pod deployed is read before it
  is terminated: a read that shows it is a sighting (recorded as seen on the run), and one that
  reads `TERMINATED` means it had ended, so nothing live was stopped and no DELETE is sent; a
  404 still sends the DELETE (list and lookup lag may hide a live pod) and counts as nothing
  live only when the 404 already means gone. So no run becomes
  final while its pod may still bill. The worker's report is read best effort from R2 before
  the stop (a cancel during that read leaves nothing stopped); once the stop is verified the
  run ends (`runs/stopped.py`): `cancelled` with `ended_at` and the end reason `cancelled`,
  unless the report said `finished` or `failed`, which ends it as the worker reported
  (answered again for a run already `cancelled`); a stop that does not show gives
  `stop_unverified` and a failed read `cloud_unreachable`, and the run stays as it was. Other
  final runs give `run_not_active`, unknown ids `run_not_found`. A verified cancel that makes
  the run final (also of `terminate_pod` and `cancel_queued_pod`) deletes the run's lease, best
  effort, after the final status is committed (§3.9 "Run scope", "Lease removal").
- **Reconcile and stragglers** (`runs/reconcile.py`, `stragglers.py`; runs lock):
  `reconcile_cloud` reads both lists once, resolves every `launching` run, and returns every
  run that was not final (as it is now) and the live `transcribe-ui-*` entries: pods in any
  status but `TERMINATED`, and queued pods by the activity check's rule (`runs/pods.py`
  `queued_pod_active`): not `FAILED`, `EXPIRED` or `CANCELLED`; a `DEPLOYED` one is skipped
  past its name deadline, is represented by its pod when that is listed as an app pod, and
  otherwise is looked up by id (`TERMINATED`, or a 404 `DEPLOY_SETTLE_S` after the deploy,
  means not live; a failed lookup gives `cloud_unreachable`), shown with its pod's id. Each
  entry carries the run id and
  deadline from its name, the local run's status, `role` (`current`: its local run is not
  final; else `straggler`), `overdue` (past its name deadline) and the response `attention`
  when any entry is a straggler or overdue. A straggler of a final run whose objects were
  collected marks that run uncollected (see "Monitoring", step 4). `terminate_pod` (`pod_id`)
  and `cancel_queued_pod` (`queued_pod_id`) read the object first (404 → `pod_not_found`; a name that is not the app's
  → `not_an_app_pod`, left alone), answer `needs_confirmation` (field `confirm`, details: kind,
  id, name, status, run id) until confirmed, then stop it verified as `cancel_worker` does (the
  pod of `terminate_pod` was read, so it is seen; `runs/straggler_stops.py`). A pod a queued
  pod deployed is recorded on the open local run that recorded that queued pod, before it is
  terminated, unless the run follows a pod this stop does not handle (a pod it adopted under
  its name, which a stop of another pod never replaces): by `cancel_queued_pod`, and, when the
  run recorded no pod yet, by `terminate_pod` when the run's queued
  pod names the pod being stopped (best effort: a failed read of the queued pod skips this and
  the pod is terminated anyway); a pod read that shows the run's pod marks it seen there, so
  a retry after an unverified stop does not wait for the deploy to settle
  (`runs/stop_records.py`). A local run of that
  id that is not final ends as `cancel_worker` ends it only when what was stopped (verified)
  includes the pod it recorded, or, without a recorded pod, its queued pod, or it recorded
  neither: a stray pod carrying the run's name, or a stop that missed the run's pod, leaves
  the run followed, and so does another live pod listed under its name (the run follows it).
  A stop that found nothing live (a `TERMINATED` pod, a queued pod ended
  without a pod, a deployed pod that had ended) leaves the run to `refresh_status`, which reads
  the worker's report. Ids follow the reaper's rule `[A-Za-z0-9_-]{1,64}`.
- **Monitoring** (`runs/monitor.py`, `refreshing.py`, `following.py`, `ending.py`, `halting.py`,
  `worker.py`, `results.py`, `ready_batches.py`, `result_reads.py`, `collect.py`,
  `lease_release.py`, `leases.py`, `progress.py`; request `refresh_status`, no fields; runs lock
  for the whole request, no transaction across a Runpod or R2 call). The GUI sends it
  periodically while a run is active, or while `awaiting_results` says so, and once at
  start-up, with or without the Runpod key. The Runpod client is opened only when a key is set
  (without one, steps 1, 2 and 4 are skipped and each open run records `credentials_missing`
  as its `stop_error`, §3.9 "Roles"); R2 is opened on first use (`runs/cloud.py` `LazyStore`),
  so missing or rejected R2 credentials never block a deadline stop. In order:
  1. Every run that is not final is followed; the Runpod lists are read once, only when such a
     run exists or a final run waits for collection. With an open run a failed read or lookup
     fails the request with nothing more changed (re-send); with none, a failed read is logged
     and only collection (step 4) is put off, so results do not stall.
     A `launching` run is resolved by its name (`resolve_launching`). A run without a pod id
     follows its queued pod: a pod listed under the run's name is adopted as seen; else the
     queued pod (listed, else read by id) decides like the settling rule (`QUEUED`: `queued`; a
     `podId`: `running` with that pod not seen; `CANCELLED`: `cancelled`; `EXPIRED`, `FAILED`:
     `failed`). A queued pod Runpod no longer knows (404) with no pod under the run's name ends
     the run (`failed`, `queue_ended`) only `DEPLOY_SETTLE_S` after the earlier of the queue's
     `expiresAt` and an acknowledged cancel (`cancel_acknowledged_at`), when a pod deployed just
     before would be listed, and only after two such lookups at least
     `LAUNCH_LOOKUP_GAP_S` apart (the first recorded in `first_empty_lookup_at`, which a later
     sighting of the queued pod clears), as for a `launching` run. A run with a pod id follows that pod (listed, else read by
     id): a read is a sighting (`pod_seen`) and records the status, the price (`cost`, USD/h),
     the creation time (`createdAt`, else now) and the first `RUNNING` read
     (`first_running_at`). The pod is gone when it reads `TERMINATED`, or is missing (404) and
     `KnownPod.gone_when_missing` holds (seen, or `DEPLOY_SETTLE_S` after its deploy: the queued
     pod's `resolvedAt`, else `pod_recorded_at`, which `update_run` sets when the run first
     records a pod id); a missing pod never seen stays followed. `EXITED` and `ERROR` pods still exist and are followed until
     gone or the deadline (the worker's entry terminates its own pod).
  2. Whether the pod is gone and whether the desktop deadline has passed are decided from the
     Runpod data alone. Then the worker's `runs/<run_id>/status.json` is read
     (`reading.read_run_status`; an invalid one or another major shows as `unreadable`). When R2
     (or its credentials) fails, a run whose pod is gone is not ended (fail closed), a run past
     its deadline is still stopped, and the R2 error is raised once every run was handled. A run
     whose pod is gone ends: `finished` when
     the worker reported `finished` (`end_reason` `worker_finished`), else `failed`
     (`worker_failed`, or `pod_gone` without a final report); the readable report is kept in
     the payload (`final_status`). Otherwise, past the **desktop deadline** (`first_running_at`
     + `MAX_RUNTIME_S` + `DESKTOP_DEADLINE_MARGIN_S` (10 min), capped by the pod name deadline,
     which alone applies while the pod was never seen running) the run is stopped exactly as
     `cancel_worker` stops it (`halting.stop_run`: `terminate_verified` with the `KnownPod`
     rules, or the queued pod cancelled and a pod it deployed recorded and terminated); only a
     verified stop makes the run `failed` (`deadline`). `stop_unverified` (or
     `launch_unresolved`) is reported as the run's `stop_error` and the run stays followed, so
     a pod that may still bill is never marked final. A stop whose Runpod call fails is
     recorded the same way (its error code as `stop_error`, the run followed); the other runs
     are still followed and recorded, and the error is raised once every run was handled.
     A run that becomes final here or in step 1 has its lease deleted, best effort, before step
     3 reads the leases (§3.9 "Run scope", "Lease removal").
  3. Every followed `ready` batch (not removed from the cloud; not only the run's: another
     computer's run may list it; its lock
     only tried, a busy one listed in `busy`) gets the status of each pending transcribe job
     whose result JSON is listed (`ok`, `no_speech`, `failed` through `reading.read_job_result`;
     a batch with neither `_done.json` nor its manifest in R2 is marked `removed_from_cloud`
     instead, §3.9),
     stored in the job's `status` column: the failed/no-speech flags; a failed result's `error` in
     the job payload's `result_error`), then moves to `transcribed` by compare-and-set when
     `_done.json` exists (HEAD, as the launch scope decides). `_done.json` is looked for before
     the results are listed: the worker writes it after the batch's last result, so a listing
     made after seeing it holds every result (one written in between is read at the next
     refresh, while the batch is still `ready`). An unreadable result stays pending (logged)
     until `_done.json` is there; then the job is `failed` with `result_error` `unreadable
     result`, and a job with no result JSON at all `failed` with `missing result` (the worker
     never comes back to a done batch), so a retry (M5.14) can take them. In the transaction that moves a
     batch to `transcribed`, the `downloaded` rule (§3.5) moves it on to `downloaded` when every job
     already has a placement outcome or failed (downloaded in full while `ready`); the response
     shows the phase. Then the rule is applied to every `transcribed` batch whose lock is free (a
     download killed after its last outcome). This step also reads the live leases
     (`runs/*/launch.json`, with the bound and finality of §3.9 "Run scope") and keeps them in
     memory with the refresh time, for `waits_for_current_run`, and the response says whether
     results are still awaited (`awaiting_results`).
  4. Every final run not yet collected (`objects_collected`; `cancelled` and `launch_failed`
     runs too, whose pod is verified gone or never existed) keeps its last status in the
     payload (if its end did not; written before anything is deleted), writes `worker.log` to
     `<log dir>/worker-<run_id>.log`, deletes every object under `runs/<run_id>/` and is then
     marked collected. Logs (`runs/worker_logs.py`) are written whole and never over another:
     to a temp file in the log dir (flushed to disk), then published with a hard link, which
     fails instead of replacing (POSIX and Windows/NTFS); on a file system without hard links
     (exFAT, FAT32, some network or FUSE mounts) by a rename on Windows (never over a file) and
     by an exclusive create (`O_CREAT | O_EXCL`) and write on POSIX. On POSIX the log dir is
     then synced (best effort), so the log is durable before the R2 objects are deleted. The
     temp file is removed afterwards and on any failure, and temp files a crash left for the
     run are removed before the next write; these removals are best effort (logged), so a
     handle another program holds never blocks a collection. A log collected again goes to `worker-<run_id>-<n>.log` with the next free n from
     2, and a log already kept with the same bytes (a retried collection) is not written again.
     An R2 or file error is logged and retried at the next refresh, without failing the
     request. A straggler pod or queued pod carrying the run's name may still write these
     objects, so collection waits while one is live in the lists read in step 1
     (`runs/lingering.py`: another id than the run's own, live by the activity check's rules:
     a pod not `TERMINATED`, a queued pod `queued_pod_active` keeps; a failed lookup counts as
     a straggler for that run, logged, and the request goes on). `reconcile_cloud` (for every straggler it lists), `terminate_pod` and
     `cancel_queued_pod` mark a final, collected run uncollected (`records.reopen_collection`),
     so the first refresh after the straggler is gone collects what it wrote, once and whole.
     Refresh never calls reconcile, so nothing loops.

  Each followed run stores what the refresh saw in its payload (`runs/refreshing.py`): the
  refresh time (`refreshed_at`), the deadline stop's error (`stop_error`, cleared by a refresh
  without one and whenever the run becomes final) and, when R2 was read, the worker report
  (`worker`; a report not read this time keeps the last one). The report is stored in its own
  plain model (`StoredWorkerReport`, unknown keys ignored) and mapped to `WorkerProgress` when
  shown (one that no longer fits shows as `unreadable`); the current file's name is looked up
  best effort (an unknown item or an unreadable item payload gives none, never an error). The
  collection of a final run stores the report it reads too.

  The response lists every run that was not final before the refresh (`RunProgress`, built from
  the stored run only (`runs/progress.py`): the run, `stage` (`launching`, `waiting` for a
  machine, `starting`, `transcribing`, `stopping` while the worker is done and its pod still
  exists, or the final status), the pod status as last read, the stored worker report (state,
  current job with its file `name` from this database (None for a batch or item it does not
  know), done/total, error), `first_running_at`, the desktop `deadline`, `elapsed_s` and
  `cost_so_far_usd` (from the pod's creation until `elapsed_until`: the run's end, else its
  `refreshed_at`, never the moment of the request; at its price; a display estimate),
  `ended_at`, `end_reason`, `stop_error`, the kept `worker_log` path, and three progress fields
  below), the checked `ready` batches with their job counts and phase, the `busy` ones, and
  `awaiting_results`.
  `cancel_worker`, and `terminate_pod` and
  `cancel_queued_pod` when they end a local run, record `ended_at` and the end reason
  (`cancelled`, or the worker's own end) too, so its time and cost stop at the stop; every final
  run has `ended_at` and an end reason.
- **Run list** (`runs/listing.py`; request `list_runs`, field `limit` 1-1000, default 20; DB
  only: no lock, no Runpod or R2 call): the latest runs, newest first, as the same stored
  `RunProgress`, so the cloud page shows the last run's outcome, end reason, worker report and
  kept log after a restart, which `refresh_status` (open runs only) cannot. A run never
  refreshed has no elapsed time until it ends.

  Progress fields (`RunProgress`, backend logic, so every frontend shows the same values;
  `runs/remaining.py` computes them, `runs/followed_jobs.py` reads the jobs, the factors are the
  launch estimate's in `runs/constants.py`). `refresh_status` computes them for every run it
  followed once the `ready` batches' results are recorded, and stores them in the run payload;
  `RunProgress` (`runs/progress.py`) shows the stored values, so `list_runs` agrees with the last
  refresh. Each refresh first clears the stored remaining time and finish with its new
  `refreshed_at`, so a refresh that fails before the computation leaves them unknown, not stale.
  - Followed audio: the launch keeps `weighted_known_duration_s`, the weighted pending audio
    (`careful` jobs counted twice, as in the estimate) of the covered batches this database
    knows. At each refresh the pending part is the weighted audio of those batches' transcribe
    jobs still `pending` here (durations of the verified audio objects, as in the manifest),
    capped at the total; every other job (ok, no speech, failed) counts as done.
  - Each recorded result keeps its run and the worker's times on it in the job payload
    (`result_run_id`, `result_started_at`, `result_finished_at`, from the result JSON).
  - `progress_fraction` (0 to 1): done over total, rounded down to 4 decimals, so 1 means
    every followed job is done.
  - `remaining_estimate_s` (whole seconds): the pending work less the time already spent on the
    file in progress, never below 0, so it counts down between refreshes and the finish stays
    put:
    - pending work: once the run has written an `ok` or `no_speech` result, the worker's pace
      (the summed `finished_at - started_at` of those results per weighted second of their
      audio; failed results do not set the pace) times the pending weighted audio, that of the
      covered batches of another computer included (their pending jobs read from the result
      listing in R2: the run's lease lists them, so its worker does them too). Before that,
      the launch estimate's runtime factors without the margin (per pending job and pass:
      audio × 0.2 + 60 s), plus the covered batches of another computer (`unknown_runtime_s`,
      the same factors over their launch-scope jobs, kept at the launch);
    - time on the file in progress, on the worker's clock (only differences of the worker's own
      times count, so clock skew does not): the report's `updated_at` minus the latest
      `finished_at` of the run's results (results of other runs are not used), else minus
      `transcribing_since` (the `updated_at` of the first report that showed `transcribing`
      with a current job, kept in the run payload: the worker reports `transcribing` before
      its model loads and names its first job once loaded; a known inaccuracy: jobs restored
      from a backup are named before the model loads, so after such a restore the time on the
      file in progress may start before the model load ends);
    - until then (no result of the run and no such report), what is left of the 20 min
      start-up allowance since the first `RUNNING` sighting is added instead;
    - 0 once the worker reported its end (`stopping`).
    - Null while no machine runs the run (`launching`, `waiting`), for an unreadable worker
      report, for a run without followed audio (none known, or launched before the field was
      kept), and once the run is final (`update_run` clears it, with `estimated_finish_at`,
      whenever a run becomes final, so a cancel or a stop clears it too; `progress_fraction`
      is kept).
    - Another computer's covered batches count in the remaining time only (their audio is not
      in `progress_fraction`): by `unknown_runtime_s` until the pace is measured, then by their
      pending jobs in R2, so the remaining time does not drop when the first file is done.
  - `estimated_finish_at`: the refresh time (`refreshed_at`) plus `remaining_estimate_s` (UTC),
    null when the estimate is. All three are as of the refresh and never extrapolate against
    now; the clock is the injected one, so the parity goldens are stable.
  - The GUI polls `refresh_status` every 300 s (`REFRESH_INTERVAL_S`, M6.6) while a run is not final, or while
    `awaiting_results` (§3.9) says so (the user can refresh by hand) and counts down to `estimated_finish_at` every second in between; a countdown
    that reaches 0 before the run is final shows "finishing" and never goes negative; each
    refresh replaces the target. The GUI computes no estimate; the CLI JSON has the same
    fields. The deadline stop still works with the 5-minute poll: it happens at the first
    refresh after the deadline, so at most 300 s late, within the 10 min
    `DESKTOP_DEADLINE_MARGIN_S` already added, and the reaper (`reaper.yml`) remains the
    backstop when the app is closed.

### 3.8 Local state, settings and concurrency

Code: `transcribe_ui/state/`. One `LocalState` per backend (in the injected `Ports`) holds the
directories, the settings store, the locks and the lazily opened database.

- **Directories**: `platformdirs` with app name `Transcribe-UI` (no author folder): data
  (`state.sqlite3`, `state.lock`), config (`settings.json`), cache (temp audio) and log (one
  file per frontend: `transcribe-ui-cli.log`, `transcribe-ui-gui.log`, later one for MCP).
  Tests and other injected roots use `<root>/{data,config,cache,log}`.
- **Schema**: every table lives in `state/models.py` (`batches`, `items`, `jobs`,
  `audio_objects`, `extracted_subtitles`, `probe_cache`, `runs`, `placements`): indexed status
  columns (phase, status, outcome, flags) plus a JSON `payload` column, so most later changes
  touch payload classes, not tables. Timestamps come from the injected clock and are stored as
  UTC. Constraint names follow a fixed naming convention (batch migrations find them by name).
  Revisions (`state/migrations/versions/`): `initial_schema` (frozen since M5 was built),
  `composition_and_run_report` (M6.1: adds `composition` to batch payloads and `worker`,
  `stop_error`, `refreshed_at`, `cancel_acknowledged_at` to run payloads as null),
  `result_error_code` (M6.14: adds `result_error_code` to job payloads, the code of the stored
  `unreadable result` and `missing result`, null otherwise) and
  `run_progress` (M6.12: adds `weighted_known_duration_s`, `unknown_runtime_s`,
  `transcribing_since`, `progress_fraction`, `remaining_estimate_s`, `estimated_finish_at` to run
  payloads and `result_run_id`, `result_started_at`, `result_finished_at` to job payloads as
  null) and `shared_bucket` (M6.13: drops `manifest_held`, adds the column `removed_from_cloud`
  (false) and the batch payload key `adopted` as null). The data dir also holds the file
  `installation_id` (§3.9 "Downloading twice"), and the backend's memory the live run leases
  the last refresh read (`state/leases.py`, §3.9 "Run scope"). Every change
  is a new revision, including any change of a stored payload's shape (a field added, removed,
  renamed or retyped, a literal value dropped; the payload models forbid unknown fields, so an
  older app could not read a payload with an added field, and the revision makes it refuse the
  database with `database_too_new` instead): the revision rewrites the stored JSON, since readers do not migrate
  it (only the probe cache treats an unreadable row as a miss). A test asserts that the
  migrated schema equals the models (Alembic `compare_metadata`).
- **Database**: WAL mode, `synchronous=FULL` (a commit survives a power loss), foreign keys
  enforced, the driver's legacy transaction control stated explicitly with its implicit BEGIN
  turned off, and every transaction started with `BEGIN IMMEDIATE`, so threads queue on
  SQLite's busy timeout (30 s) instead of failing on a read-to-write upgrade; a transaction
  still waiting after it (`SQLITE_BUSY`) gets `state_busy` (re-send). One session (one
  transaction) per unit of work (`LocalState.session`); sessions do not nest, and handlers
  keep transactions short.
- **Opening**: the dispatch table marks which requests need the database; `Backend.execute`
  opens it before such a request runs, the first time one arrives. Opening takes the writer
  lock, then runs the migrations (`render_as_batch=True`, foreign keys off during migration) in
  one transaction, which ends with `PRAGMA foreign_key_check`: a violation rolls the migration
  back (`state_unavailable`, details in the log). The Alembic environment (`env.py`,
  `script.py.mako`, `versions/`) is package data found through `importlib.resources`; there
  is no `alembic.ini` (the config is built in code). A database carrying a revision this app
  does not know is refused unchanged with `database_too_new`. A failed opening holds nothing;
  the next request tries again.
- **Writer lock** (cross-process): an OS lock on `state.lock` (`fcntl.flock` on POSIX,
  `msvcrt.locking` on Windows), never waited for; the OS releases it when the process ends.
  Another process (or another backend in the same process) gets `app_busy` for any database
  request, while requests that need no database (app info, settings, credentials, scans,
  cloud setup) are served, so the CLI works while the GUI is open.
- **Batch locks** (in-process, one per batch): every request that changes a batch takes its
  lock; it is never waited for: a held lock gives `batch_busy`. Work that skips a busy batch
  instead of failing (`refresh_status` checking the `ready` batches) uses `try_batch`, which
  yields whether the lock was taken.
- **Runs lock** (in-process, one): taken by the requests that launch, follow or clean up runs,
  by the cleanups and by the withdrawals of a leftover manifest; waited for at most `RUNS_LOCK_WAIT_S` (30 s, checking the cancel
  token), then `runs_busy` (re-sending resumes). The registry records the holding thread, so
  code that must run under it (the activity check, a withdrawal) checks
  `holds_runs` and raises `LockOrderError` otherwise.
- **Lock order** is runs → batch → database transaction (innermost). A request holding batch
  locks takes the runs lock only through the bounded wait, so no two requests can wait on each
  other. Never wait for a lock or do network I/O inside `state.session()`: asking for a batch or
  the runs lock, or for a second session, while the thread has a session open raises
  `LockOrderError` (a programming error, so `internal_error`).
- **Phase changes** are compare-and-set (`compare_and_set_phase`: one `UPDATE … WHERE phase =
  :expected` plus optional conditions): no row changed gives
  `phase_conflict` (or `batch_not_found`), and the error rolls back the request's whole
  transaction, so a request that lost a race changes nothing. A batch row the session already
  loaded is expired after the change, so it reads the new phase.
- **Settings** (`get_settings`, `update_settings`, `reset_settings`; no database): enabled languages (default
  `DEFAULT_LANGUAGES`, at least one, validated against `WHISPER_LANGUAGES`, no duplicates),
  video and audio extensions (defaults and rules shared with the scan, §3.2), include hidden
  entries, the ffmpeg/ffprobe path override (absolute in the Windows or the POSIX sense, so the
  rule does not depend on the machine; the tool lookup also checks it on this computer),
  placement defaults (strategy, output folder, folder count, overwrite) and the language of the
  texts (`system`, `en`, `hu`; §3.1). `update_settings`
  replaces the whole document: every field is required, so a partial document is refused with
  `invalid_request` instead of resetting the missing fields. `reset_settings` stores the
  defaults (also over a file that cannot be read; the GUI's Restore defaults, M6.3). Every
  response lists every Whisper language with its switch and whether WhisperX has an alignment
  model for it.
- **Settings file**: written atomically (temp file in the same folder, fsync, `os.replace`,
  then a best-effort fsync of the folder on POSIX, which only logs a warning when it fails);
  temp files a crashed write left (older than a minute) are removed; the last writer wins. On
  Windows, reads and the replace are retried for about 2 s while another process has the file
  open. Reading starts from the defaults and takes the fields the app knows: a missing file
  means the defaults, fields of a newer app are ignored and fields an older file lacks take
  their defaults; saving from an older app therefore drops the fields it does not know. An
  unreadable or invalid file gives `settings_invalid` until settings are saved again; a failed
  write gives `settings_unwritable`. Readers: `get_settings`/`update_settings`, the GUI (to
  fill request fields), the media tool lookup (only the ffmpeg/ffprobe paths, §3.4) and each
  frontend's language choice at start-up (only `language`, §3.1). Other
  request handling never reads it: machine-dependent inputs (scan options, placement options,
  test mode) are request fields.
- **Logging**: a frontend wraps its run in `log_to_file(<its log file>)`: a human-readable
  rotating file (1 MiB, 3 backups, UTF-8) in the user log dir, the app's loggers from INFO,
  other libraries from WARNING, warnings captured. One file per frontend keeps the GUI and the
  CLI from rotating one file; two runs of one frontend may still share it, which the handler
  (`SharedRotatingFileHandler`) tolerates: rotation renames the file first and shifts the
  backups only after that worked; when the rename fails (Windows, another process has the file
  open) it keeps appending and retries after another 64 KiB; before each record it reopens the
  path when another process rotated the open file away (POSIX); a `.rotating` file left by an
  interrupted rotation becomes the newest backup. No error leaves the handler and nothing
  reaches stderr: a record that cannot be written is dropped and the next one opens the file
  again; after `close`, records are dropped. The log dir is created with the first
  record. An unexpected exception's traceback goes only there (§3.1).

### 3.9 Several installations on one bucket

Requirements "Runpod control" (Q28): installations on several computers share one bucket, and
not all of them hold the Runpod key. Built in M6.13 (`plans/m6.md`). The answers it rests on are
Q28, Q35, A21 and A22.

- **Roles.** Every installation needs the four R2 credentials; the Runpod key is optional.
  - An installation without it (R2-only) composes, reviews, uploads, checks for results,
    downloads and cleans up. Cloud setup, Start transcription, Cancel and Stop, and the cloud
    machines list need the key; the GUI shows them as unavailable with the reason (another
    computer with a Runpod key starts transcription for every uploaded job).
  - The credential store and `credentials_status` treat each credential on its own,
    `check_credentials` reports Runpod `missing` without failing, and `setup_cloud`,
    `plan_launch`, `launch_worker`, `cancel_worker`, `reconcile_cloud`, `terminate_pod` and
    `cancel_queued_pod` need the key.
  - `run_upload`, `create_retry_batch`, `cleanup_cloud`, `exclude_items` and the re-review open
    R2 alone (`runs/cloud.py` `open_store`): the manifest step needs no check, and cleanup and
    the withdrawal of a leftover manifest use the per-batch lease check ("Run scope" below).
  - `refresh_status` opens the Runpod client only when a key is set (`runpod_key_set`).
    Without one, steps 1 and 2 are skipped and each local run left open records
    `credentials_missing` as its `stop_error`; step 3 runs (results and the live leases);
    collection (step 4) is put off, as when the Runpod lists cannot be read.
- **Run scope** (A21, decided: option 2; replaces the held manifests and the activity check
  before publishing).
  - Lease: the launching desktop writes `runs/<run_id>/launch.json` (contract `RunLease`: the
    covered batch ids and the pod name deadline) after committing the run row and before the
    single `POST /v2/pods` (`runs/starting.py`). A failed lease write makes the run
    `launch_failed` with nothing created. Two computers with a key can pass their activity
    checks at the same moment, so after writing its lease the launch lists the leases again: a
    lease of another run written less than `LAUNCH_SETTLE_S` ago (`concurrent_leases`) makes
    the run `launch_failed` with `run_active` before the POST (both may back off; Start again
    then works for one of them). The worker (§5.3) processes only the batches listed
    there; without a readable lease it processes nothing, reports a failed status and its entry
    ends the pod. A listed batch whose manifest has disappeared (a cleanup between the lease
    and the start) is skipped, not a failure. What a run does not list is never processed by it
    (nor its `_done.json` written or its backups restored), whatever any computer publishes
    later.
  - Publishing: a manifest is written as soon as an upload is verified, on any installation,
    without an activity check (`upload/publish.py`). `manifest_held` and
    `publish_held_manifests` are gone; the revision `shared_bucket` drops the flag, and a batch
    still held at the upgrade stays `uploading` and is published when its upload is resumed
    with `run_upload`.
  - Live lease (`runs/leases.py`): a lease counts until its deadline, and no longer once the
    run's `status.json` is final or no longer written (older than `HEARTBEAT_STALE_S` +
    `HEARTBEAT_MARGIN_S` by its `LastModified`: a dead worker, the rule of
    `runs/heartbeats.py`); an unreadable lease too. Every lease is bounded by its R2 `LastModified` +
    `LEASE_MAX_SPAN_S` (the longest wait + the longest runtime + the name deadline margin +
    `HEARTBEAT_MARGIN_S`); a readable deadline is capped at that bound, an unreadable lease
    inside it counts and lists every batch, and an older lease never counts. Reading fails
    closed (an R2 error is raised, never "no lease").
  - Lease removal (`runs/lease_release.py`): every request that can end a run
    (`launch_worker`, `cancel_worker`, `terminate_pod`, `cancel_queued_pod`, `reconcile_cloud`,
    `refresh_status`) notes the final runs when it starts and, under the runs lock, outside any
    transaction and after the final status is committed, deletes the lease of every run that
    became final since (a run the request created included: the launch's own `launch_failed`).
    A run becomes final only while its pod is verified gone or never existed (§3.7), so this
    covers every ending path: the verified cancel and stops, the deadline stop, a pod gone in
    refresh step 2, the queued-pod endings of step 1, `resolve_launching` and the launch lookup.
    `refresh_status` releases after step 2, before it reads the leases in step 3. R2 is opened
    lazily; an R2 or credentials failure is logged and never fails or reverts the request
    (collection, which deletes all of `runs/<run_id>/`, or the lease's bound end it). An
    ambiguously failed lease write that stored the object is covered by the same rule. So a run
    that never writes `status.json` (cancelled while queued, `launch_failed`) does not block
    Start here or on other computers until its deadline; a straggler that starts later finds no
    lease and processes nothing.
  - `waits_for_current_run` (batch label, "uploaded, waits for the current run", requirements
    Q27): on a `ready` batch while a live run is known and none lists it
    (`batches/run_scope.py`). Known: the local runs that are not final (their covered batch
    ids) and the live leases `refresh_status` step 3 last read, kept in the backend's memory
    with the refresh time (`state/leases.py`, carried in every session's `info`; leases of runs
    that are final here since are left out; no database change, the start-up refresh fills it
    again), so no list request reads R2. An unreadable lease lists every batch, so it makes no
    batch wait. The label is as of the last refresh; the poll keeps it current while results
    are awaited.
  - Activity: `cleanup_cloud` and the withdrawal of a leftover manifest (§3.3) use only the
    per-batch lease check (`runs/leases.py` `refuse_if_leased`: a live lease that lists the
    batch gives `run_active` with the lease as its sign; no Runpod lists), so they work on every
    installation. `check_activity` (a local open run, the Runpod lists, heartbeats, any live
    lease: kind `lease`) serves only the launch (one run at a time; another computer's live
    lease counts).
  - Withdrawals: the launch no longer finishes pending withdrawals (its `batch_busy` refusal
    for an unfinished one is gone). The scope counts a leftover whose pending withdrawal is
    recorded as absent, so no lease of this computer lists it; it is finished, under the runs
    lock and while no live lease lists the batch, by the next exclusion or re-review, the start
    of `run_upload`, the manifest step (refused with `run_active` while a lease lists the
    batch) and the extract-only transition. A withdrawal holds the runs lock from its lease
    check through recording the `PendingWithdrawal`, which the "Remaining window" (§3.3)
    relies on.
  - Versions: the lease is required by the worker, yet M6.13 stays on contracts 1.1 (no major
    bump): no desktop is released before M8, and every Runpod run (M4, M7) uses the current
    build. A Runpod run of this build needs the new schema-1 worker image pushed first: an old
    image ignores the lease, which breaks A21 silently (no billing risk).
- **Known batches.** A batch in R2 counts as this database's (`known`) only when its id is in
  the database and the batch was adopted (below) or its manifest is recognised as this
  database's (`upload/leftover.py` `is_known`: the digest a pending withdrawal recorded, or the
  same `created_at` and `scan_root`). Another computer's batch that happens to have the same id
  is unknown. This applies to the launch scope (§3.7), `list_cloud_batches` and the cleanup: a
  `cleanup_cloud` on an id this database knows runs its own checks and confirmations, then goes
  to the unknown batch's path when R2 holds a readable manifest of another batch under the id
  (no manifest, or one that does not read, keeps it this database's).
- **The start** (an installation with the key). `plan_launch` lists every manifest without
  `_done.json`, the unknown ones separately (id, files, audio length), counts them in
  `MAX_RUNTIME_S` and the cost estimate, and each can be deleted from the cloud with one
  confirmation (`cleanup_cloud` on an unknown batch, §3.3). The run's lease lists all of them.
- **Results poll.** `refresh_status` step 3 checks `_done.json` and the results of every
  followed `ready` batch, whoever started the run, so the uploader places the subtitles (next
  to the originals included) as for a run it started. Its response says whether results are
  still awaited (`awaiting_results`: some `ready` batch not removed from the cloud); the GUI
  polls every 300 s while that or an open run says so, with or without the Runpod key, and on
  "Refresh now". The GUI does not derive the condition itself.
- **A batch removed from the cloud by another computer.** Step 3 looks for `_done.json` first;
  a `ready` batch with neither `_done.json` nor its manifest in R2 (this database's own cleanup
  moves a batch to `cleaned` first) is marked by the column `removed_from_cloud`
  (`runs/ready_batches.py`) and shows the batch label of that code ("removed from the cloud by
  another computer"). Its stage is `done`; its actions allow only hiding (`hide_batch`,
  accepted by `set_batch_hidden`) and placing extracted subtitles (the downloads plan only its
  extract jobs, as for `cleaned`); `cleanup_cloud` refuses it (`batch_removed_from_cloud`). It
  no longer counts for `awaiting_results`, is no longer checked, and its history stays.
- **Download of another installation's job.**
  - `list_cloud_batches` (`batches/cloud_jobs.py`; R2 only): every manifest in R2 that reads,
    with its id, `known`, file count, job count, audio length (each file's first audio object),
    the jobs with a result, and its state (`waiting`: no result; `transcribing`: some results,
    no `_done.json`; `transcribed`: `_done.json`).
  - `adopt_cloud_batch` (`batches/adoption.py`, `adopted_rows.py`; one confirmation, code
    `adopt_cloud_batch`): refuses an id this database already uses (`batch_exists`), reads the
    manifest (`reading.read_manifest`: `batch_not_found`, `manifest_unreadable`) and records the
    batch `ready`, created when the manifest was, with the payload's `adopted`
    (`AdoptedBatch`: the manifest's `uploaded_by`) and the label `adopted`; its items `uploaded`
    with each `original_path` as stored and the relative path, its jobs pending, its audio
    objects recorded as verified (the remaining time reads their durations). From then on it is
    followed by `refresh_status` and downloaded like any batch, again any number of times (Q25).
  - The manifest is untrusted input. Each item's path below the scan root is split with the
    manifest's `path_flavor`; adoption refuses the manifest (`manifest_path_invalid`, naming the
    item) when a part is no single name on every OS (`placement/naming.py` `unsafe_part`:
    empty, `.` or `..`, a NUL, `/` or `\`, or a drive or an anchor; a POSIX part `..\..\x` or
    `C:evil` is refused). The placement planner splits sources and the scan root with the
    batch's own `path_flavor` (`TargetNames.source_flavor`) and only targets with the target
    rules; for any batch it refuses a source whose relative part below the scan root is empty,
    `.` or `..`, or holds a separator, drive or anchor of that flavour (`source_path_invalid`);
    every other character of a folder or file name it copies into a target (`mirror`,
    `flat_with_folders`) goes through §3.5's name rules, which make it valid on the target OS.
    The contract's own check (`Manifest._check_paths`) refuses `.` and `..` parts in `scan_root`
    and every `original_path`, split as written in the manifest's flavour (no schema version
    change: the desktop never wrote such paths).
  - Placement: `mirror` into a chosen folder (the folder takes the place of the scan root, so
    the original relative folders and file names are kept), `flat`, `flat_with_folders` and
    custom work from the relative paths; `next_to_media` is refused (plan error
    `no_local_sources`), since the originals are on another computer. In the custom plan,
    `source_path` is the manifest's `original_path` and `original_folder` its parent in the
    uploader's path flavour; they identify the file and are not checked on this computer.
  - Not available for such a batch: retry (the uploader decides) and the "audio" cleanup
    (`adopted_batch`, and not in its `actions`); it has no subtitles extracted from inside video
    files (they are made and kept only on the uploading computer, §3.3).
  - The manifest keeps each file's absolute original path, as the requirements ("UI app:
    conversion and upload") ask, so every bucket user can read the uploader's paths (A22,
    decided: kept; no contract change). Other computers derive the relative paths locally and
    refuse unsafe parts (above).
- **Downloading twice.** Results leave R2 only through `cleanup_cloud` (manual; `everything`
  asks a second time while this database has a job without a placement outcome; an unknown
  batch's `everything` asks once, with no download check). Nothing deletes results on its own,
  so both installations can download in any order and as often as they like. The remaining
  risk is a manual "everything" on one computer before the other one downloaded (Q35):
  - Installation id (`state/installation.py`): a random ULID, not hardware-based, from its own
    injected port (`Ports.installation`, not the shared id source, so goldens and M6.11's
    id-draw matching do not shift), kept as the file `installation_id` in the data dir. It is
    created on first use and written atomically (a temporary file, fsync, then `os.replace`); a
    file that does not hold a valid id is replaced the same way (the only cost: this
    installation's earlier uploads then get the stronger warning). The requests that use it
    have opened the database, so the process holds the writer lock; a lock in the object
    serialises its threads. The architect asked for a machine ID; an id per installation
    identifies the uploader without hardware access, and two installations on one machine are
    distinct uploaders.
  - Every manifest the manifest step builds names it as `uploaded_by` (the worker ignores it,
    and older readers ignore unknown fields). The run lease and `uploaded_by` share one bump to
    1.1; `SCHEMA_VERSION` is one constant, so the worker's results are written as 1.1 too.
  - The "delete everything" confirmation has two forms (two codes). The plain one
    (`cleanup_everything`, `cleanup_unknown_batch`) when this installation uploaded the job: a
    known batch that was not adopted (the database proves it, also for a repeated cleanup
    after the manifest is gone), an adopted batch whose `uploaded_by`, stored at adoption, is
    this installation's id, or an unknown batch whose manifest names it. Otherwise (an adopted
    batch of another computer or without the field, an unknown batch of another computer or
    without the field) the stronger form (`cleanup_everything_uploaded_elsewhere`,
    `cleanup_unknown_batch_uploaded_elsewhere`) adds: "Another computer uploaded this job; it
    may still need to download the subtitles." It is a warning, never a refusal; the GUI shows
    it emphasised (`gui/cleanup/constants.py` `WARNING_CONFIRMATIONS`).
- **Batch ids.** Ids are unique only per database; an upload already checks that nothing exists
  under its id in R2 before its first object (§3.3 upload), so two computers creating a batch in
  the same second do not mix.

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
runs/<run_id>/launch.json                 the run's lease (1.1): the batches it may process, written by the launching desktop
```

- Single-writer rule: every object has exactly one writer (desktop or worker), so no
  read-modify-write races exist. `runs/<run_id>/` is worker-written except `launch.json`, which
  only the launching desktop writes (and deletes once the run is final); collection deletes
  both.
- Versioning of the 1.1 additions (the lease, `uploaded_by`): no major bump; §3.9 "Run scope"
  gives the reason.
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
`audio_object_key`); from 1.1 the optional `uploaded_by`, the uploading installation's id
(§3.9, Q35). `scan_root` and every `original_path` have no `.` or `..` part, split as written in
the manifest's flavour.

Run lease (1.1, `RunLease`): `schema_version`, `run_id`, `batch_ids` (at least one, each once),
`deadline_at` (the pod name deadline).

Implementation: `packages/contracts` (`classes.py` models, `layout.py` key builders/parsers,
`reading.py`). Readers always go through `reading.read_*(key, body)` (`read_run_lease` for the
lease): it refuses another major version before validation (`UnsupportedSchemaError`) and checks
that the object's batch / item / language / run id matches its key. Stored objects accept any well-formed language code (forward
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
   commands. The public run log may show pod ids, run ids, deadlines and action times
   (architect decision, `requirements.md` "Failure handling"). Each call is retried a few times within the pass. The queued phase starts no new
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

- Discover (A21, §3.9 "Run scope"): read `runs/<run_id>/launch.json` and load the manifests of
  the batches it lists, skipping those with `_done.json` (`pipeline/plan.py`). Without a
  readable lease the run processes nothing and fails (`RunLeaseError`; the entry then ends the
  pod); a listed batch whose manifest is gone is skipped. Backups are restored only for the
  listed batches' pending jobs.
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
  width (42 characters for every language, requirements "Deployed app (worker)"); the n-gram quality check splits on spaces, so it does not see loops inside unspaced
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

One R2 API token scoped to the single bucket (R2 cannot scope by prefix), shared by the
desktop and the pod (A18). `setup_cloud` creates or updates the Runpod secrets from the R2
credentials in the desktop keyring, then the template that references them (§3.7); rotating
the token means re-running `setup_cloud`. The image and the pod-create request never contain
credentials.

## 6. Cross-cutting

- Typing: mypy strict with the pydantic plugin; Pydantic v2 models are frozen where possible.
- Errors: domain exceptions per module; frontends map them to error responses
  (`{"error": {"code": ..., "message": ..., "details": ...}}`), never stack traces.
- Logging: stdlib `logging`; JSON lines in the worker (uploaded per run), a human-readable
  rotating file in the user log dir in the desktop app (§3.8). Secrets are never logged; the worker
  never logs its environment (it contains `RUNPOD_API_KEY` and the R2 secrets).
- R2 client: boto3 with `request_checksum_calculation="when_required"` and
  `response_checksum_validation="when_required"` (botocore ≥ 1.36 default checksums are not
  reliably supported by R2), region `auto`, multipart ≥ 64 MiB parts; 6 attempts per request
  (botocore `standard` mode, `total_max_attempts=6` in both the worker and
  the desktop), 10 s connect and 30 s read timeouts. The worker and the desktop each
  have their own adapter (apps share only contracts) with the same settings.
  - Desktop (`storage/`): `ObjectStore` protocol with `check_access`, `head`, `list_objects`,
    `read`, `write`, `write_new` (`If-None-Match: *`), `copy` (server side; multipart copy
    from 64 MiB), `delete` (missing = success), `abort_uploads` (every unfinished multipart
    upload under a prefix) and `upload_file`. The client factory is injected with the
    client's limits; each client gets its own boto3 session (the default session is not
    thread-safe), built inside the error translation, so a broken AWS profile is an `r2_*`
    error too.
  - A HEAD response has no body, so `head` returns None for a missing bucket as for a missing
    object; `check_access` or `read` tells them apart, and `copy` (whose managed transfer
    starts with a HEAD) lists the bucket on that error path to report `r2_bucket_not_found`.
  - Retries after a lost response make two outcomes ambiguous: `write_new` returns False
    when its own retried PUT gets 412, and a retried `CompleteMultipartUpload` can fail with
    `NoSuchUpload` although the object was written. Callers verify with `head` (size) or by
    comparing the stored content.
  - `upload_file`: below 64 MiB one PUT, else a multipart upload (refused before it starts
    with `r2_object_too_large` beyond 10 000 parts, 625 GiB) whose parts are sent one after
    the other, with progress after each part and the cancel token checked before each.
    A cancelled or failed multipart upload (a failing progress callback included) is aborted
    (a failed abort is only logged; the next `run_upload` aborts stale uploads); parts are
    never resumed across requests, an interrupted object is uploaded again from its start.
  - SDK failures become `StorageError` (a `DomainError`, raised `from None`) with a fixed
    message per code and only the operation and the S3 error code or exception type as
    details; the SDK's own texts (URLs, bucket, parameters) are never passed on. The SDK's
    loggers (`boto3`, `botocore`, `s3transfer`, `urllib3`) are held at WARNING: their debug
    records carry request headers with the access key id.

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
  - End-to-end flow (`tests/test_end_to_end.py`, goldens `end_to_end_*`): create, review and
    upload a batch (manifest published), plan and launch the worker (fake Runpod API), refresh
    while a simulated worker (`support/worker_sim.py`) writes its heartbeat, then its results
    (ok, no speech, failed), `_done.json` and log, and its pod ends; download with `mirror`
    (`downloaded`), retry the failed and no-speech jobs (`careful`, audio copied), clean up the
    source's audio, then everything, then the retry batch (two confirmations). Interruptions
    and every placement strategy are covered by the per-request flows (upload, download,
    retry, cleanup), not repeated here. Its steps (`STEPS`), the worker's outcome per job and
    the worker's changes between the refreshes live in `support/end_to_end.py`, shared with the
    GUI flow.
  - GUI end-to-end flow (`tests/test_gui_end_to_end.py`; `support/gui_flow.py`,
    `support/flow_steps.py`, `support/flow_window.py`): a user drives the real window over its
    own leg (`LaunchLegs` with another bucket prefix), whose settings are stored beforehand
    with English before Hungarian (the job's languages follow the user's order and the CLI flow
    sends English first; the store is not a request of the flow). The user tests the accounts,
    checks the cloud setup, scans and ticks the media folder, chooses the languages (the silent video
    in Hungarian only), creates the job (confirmed), continues to review, uploads, starts the
    transcription, lets the poll's manual timer fire three times (the simulated worker changes
    the cloud before the second and the third), downloads with `mirror` into a typed folder,
    retries the ticked transcriptions, opens the start dialog for the retry's plan, and cleans
    up through the cleanup question three times. A recording runner between the window's
    executor and the backend (also the backend's id source) keeps every request that reaches
    the backend, its answer and progress events, the ids drawn on its thread and the clock when
    it started.
    - Matching: walking the requests in start order, a request is the next step's when it has
      the step's action and, like the step's golden answer, asks for a confirmation or not (the
      GUI asks before the cleanup the CLI flow confirms at once). Any other request must change
      nothing: a read-only action (`get_*`, `list_*`, the credential and setup checks, scans,
      previews, plans), an answer `needs_confirmation`, or a cloud check whose typed answer
      reports nothing (a refresh without runs, batches or manifests; a reconcile without
      entries, runs or `attention`); otherwise the test fails naming it.
    - The matched requests (paths normalised to `<root>`) are replayed through the parity
      runner on fresh legs built like the CLI flow's, with the worker's changes before the same
      steps; each response must equal its `end_to_end_*` golden byte for byte, and so must the
      GUI's own outcome of the step (its answer, events and exit code, normalised), so a step
      the GUI got answered differently (an error, say) fails too. The replay's
      backend leg records its draws and clock per step: the GUI's draws since the previous
      step (its `scan_tree` draws the scan id the replayed `create_batch` draws) must be the
      step's draws, and each step must start at the replay's clock; a mismatch names the GUI
      request. The flow must finish within `FLOW_BUDGET_S` (60 s).
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
- **Runpod client tests**: a stateful fake Runpod API (`tests/support/runpod_api.py`, served
  through `httpx.MockTransport` on the real host name, so nothing leaves the process) with
  injectable statuses, failed connections, responses lost after the create took effect,
  redirects, list lag, queued pods deploying on a schedule (or at a set moment, such as during a
  cancel), terminated pods that leave the list and read 404 as documented (or, with
  `keep_terminal`, stay listed), terminates that do not take effect, a create answer with
  another `status`, a hook that
  pauses a request (thread races) and no capacity. The parity runner builds a fresh fake per leg (`support/runpod.py`). No
  automated test calls the real Runpod API, not even read-only.
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
- **GUI**: view-model tests on `SyncExecutor` (no display); pytest-qt tests of the bridge, the
  channels, the shell, closing and the entry point over a gated fake runner
  (`support/gated.py`: records every call, holds chosen actions until released) or a seeded
  backend; a third parity leg, `gui` (`support/gui_parity.py`, legs from `support/parity.py`):
  requests through `QtExecutor` must give the backend leg's response and events in order (and
  the shared goldens). The `app_busy` notice is tested against a second process holding the
  lock (`support/lock_holder.py`). Each area has view-model tests, request goldens
  (`gui_<area>_*`, replayed through the backend and CLI legs) and widget tests in the window.
  The GUI end-to-end flow (`tests/test_gui_end_to_end.py`) drives the real window through
  `QtExecutor` over a leg built like the CLI flow's (the same factory, tree, fake media, moto,
  fake Runpod API and simulated worker, the clock advanced at the same points, the poll on an
  injected manual timer) and compares it with the CLI flow: the CLI flow's steps must come in
  order, nothing else may change anything, their replay must give its goldens, and both flows
  must draw the same ids at the same clock (below). Tests run offscreen (`QT_QPA_PLATFORM`
  defaulted in `conftest.py`); no screenshot comparison.

## 8. CI/CD (summary; details in `tooling.md`)

- `ci.yml` runs on every pull request and push to `main` (no workflow-level path filter, so
  required checks always report). A first job detects which members changed; lint, typecheck
  and test jobs per member run only when needed (the desktop member on Ubuntu and on
  `windows-2025`); one aggregate `ci-ok` job is the single
  required status check. gitlint checks PR commits.
- `release-desktop.yml` on push to `main` when desktop/contracts code changes: PyInstaller
  one-file builds on Windows, macOS arm64, macOS x86_64 and Ubuntu 22.04, two per platform:
  the GUI build (windowed, with Qt) and the CLI build (console, without Qt; it must never
  import Qt; it is small and fast to start). Smoke-test both builds: the CLI build's
  `--version`, and the GUI build's start-and-quit (offscreen platform, on every OS). Upload only the executables
  (`actions/upload-artifact` with `archive: false`) and attach the eight executables (file names unique per OS and variant) to a
  GitHub Release. Builds are unsigned for now (macOS
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
  restricted to `main` the same way (the key's scope: Pods read+write only). It installs only the worker package without the `engine`
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
  `research/runpod.md` "Items to test with a real key"). Deferred until everything else works
  (architect); it still needs the architect present and their go.
- **M5** (built; planned in `plans/m5.md`): Desktop backend: façade,
  discovery and selection, batches, conversion and resumable R2 upload, credentials, worker
  launch and monitoring, download and placement, retry and cloud cleanup (§3.1-3.7).
- **M6** (planned in `plans/m6.md`): Desktop frontends: GUI and CLI over the same requests
  (§3.1), and several installations on one bucket (§3.9).
- **M7**: End-to-end run from the desktop app, with the architect present.
- **M8** (planned in `plans/m8.md`): Desktop release: `release-desktop.yml` builds two one-file executables (GUI and CLI) for each of the four
  platforms, eight files (§8; the workflow does not exist yet); both builds are smoke-tested (CLI `--version`, GUI offscreen start and quit, and on Linux
  the CLI's `--help` with `LANGUAGE=hu` showing an app help text in Hungarian) and bundle the `.mo`
  catalogs (§3.1 "Language of the texts").
- **Later**: MCP frontend over the same requests.
