# Tooling

## Environment

- uv manages Python (3.13, `.python-version`) and the workspace. `uv sync --all-packages`
  installs every member plus the root `dev` dependency group (plain `uv sync` installs only
  the root project and the contracts, not the apps).
- Credentials for local integration work live in an uncommitted `.env` (see `.gitignore`,
  mode 600); CI uses GitHub repository/environment secrets. Local names:
  `RUNPOD_API_KEY_READONLY` (a Runpod key created with "Read Only" permission; usable without
  the architect, e.g. to list pods or read the GPU catalog). Anything that creates, changes or
  deletes on Runpod (including starting pods) needs the architect present, and no other key
  falls under the exception. Pod responses can contain `env` values (R2 secrets): never print
  or log them.

## Lint and typecheck

Taken from `ApplicantFilterSystemO/manager_backend` (see `research/reference-lint-config.md`)
and adapted. Both linters are needed: ruff does not implement wemake-python-styleguide (WPS),
flake8-assertive, flake8-spellcheck, flake8-pydantic, several bugbear rules (B036, B037,
B040–B042, B902, B906–B908, B910, B950) and a few print/annotation/naming rules.

| Tool | Config | Notes |
|---|---|---|
| `ruff format` | `pyproject.toml` `[tool.ruff.format]` | double quotes, 120 columns |
| `ruff check` | `[tool.ruff.lint]` | `select = ["ALL"]`, WPS declared `external`, mccabe 8 |
| `flake8` | `.flake8` | plugins pinned in the `dev` group; spellcheck words in `whitelist.txt` |
| `mypy` | `[tool.mypy]` | `strict = true` + `pydantic.mypy` plugin |

Deviations from the reference: `W503`, `E704` and `E701` ignored (they contradict
`ruff format`; setting `ignore=` replaces flake8's default ignore list), ruff mccabe aligned to
8, `exclude` added to `.flake8`, `pandas` dictionary dropped, fresh `whitelist.txt`, pytest
`--import-mode=importlib`. Tests additionally ignore `WPS202` (module members), `WPS221`
(Jones complexity: assertions read better inline) and `N802` (`http.server` handler names in
fakes); ruff's `INP001` is ignored for `tests/` and `scripts/` (no `__init__.py` on purpose).

Spellcheck: add genuine technical words to `whitelist.txt` (one per line, lower case) instead
of `noqa`. Any `noqa` needs the specific code and a reason in a comment.

Commands (from the repo root):

```
uv run ruff format .
uv run ruff check .
uv run flake8 .
uv run mypy <member src dirs>
uv run pytest <member tests dir>
```

`uv run scripts/check.py [--fix] [target ...]` runs all of these per target in parallel (the
repo tooling and each workspace member at the same time; steps within a target run in order,
with a separate mypy cache per target) and prints one block per target. CI runs the same script
per changed target. No pytest-xdist: the suites are small and process start-up would eat the
gain. The `tooling` target (`.claude/hooks`, `scripts`) runs `scripts/tests` (the pure parts of
the build and smoke scripts; pytest's `pythonpath` has `scripts`, so they import the scripts as
top-level modules); a member's `packaging/` folder is type-checked with its sources (the
desktop's PyInstaller hooks and runner, against `types-pyinstaller`).

Schema snapshots of the contract (`packages/contracts/tests/snapshots/`) are regenerated with
`UPDATE_SNAPSHOTS=1 uv run pytest packages/contracts/tests`; review the diff. The desktop's
`Request`/`Response` schema snapshots (`apps/desktop/tests/snapshots/`) and golden files
(`apps/desktop/tests/golden/`) are regenerated the same way with
`UPDATE_SNAPSHOTS=1 uv run pytest apps/desktop/tests`. On a rebase conflict in a snapshot or
golden file, regenerate it; never merge it by hand.

The desktop's R2 tests use moto behind the app's own client settings: `conftest.py` sets
`MOTO_S3_CUSTOM_ENDPOINTS` to the test endpoint `https://r2.example.invalid` before moto loads
its S3 routes, so a test without the mock fails on DNS instead of reaching a network. Error
mapping uses botocore's `Stubber`. The credential and storage tests use the `conftest.py`
fixture `no_credential_logs`, which captures DEBUG records and fails on any test credential. Golden files of the credential requests are named after the
action first (`store_credentials_*.json`): `.gitignore` drops `credentials*.json`.

Desktop tests never call the real Runpod API (not even read-only): the Runpod client gets an
`httpx.MockTransport` over the fake API in `apps/desktop/tests/support/runpod_api.py`, and
retry waits are recorded, not slept.

Desktop tests import their helpers as `support.*` from `apps/desktop/tests/support/` (pytest
`pythonpath` in the root `pyproject.toml`; `support` is first-party for isort). The desktop CLI
runs as `uv run transcribe-ui ...` (`schema`, `run <request.json | ->`, `credentials`,
`--version`) after `uv sync --all-packages`.

The GUI runs from source as `uv run transcribe-ui-gui` (a `[project.gui-scripts]` entry) after
`uv sync --all-packages`. Its log is `transcribe-ui-gui.log` in the user log
dir, and it uses the real per-user state like the CLI.

GUI tests (pytest-qt, `qtbot`) run headless: `conftest.py` sets `QT_QPA_PLATFORM=offscreen`
with `setdefault`, so `QT_QPA_PLATFORM=xcb uv run pytest apps/desktop/tests/test_gui_shell.py`
(or `windows`, `cocoa`) shows the windows. No Xvfb, no screenshots. Tests wait on the event loop
with `qtbot.waitUntil` (`support.gated.wait_for`), never with sleeps; the gated fake runner
(`support/gated.py`) holds chosen actions until released, so pool and cancel states are
deterministic.

Desktop test backends come from `support.fakes.seeded_ports(root)`: fixed clock, seeded ids and
the local state under `<root>/app/{data,config,cache,log}`; close `ports.state` when a test opened
the database (the parity runner closes its legs). The CLI run by hand uses the real per-user
directories (architecture §3.8); its log is `transcribe-ui-cli.log` in the user log dir. Every
schema or payload change is a new Alembic revision (architecture §3.8; the existing ones are never
edited), so a local database upgrades on its next opening; `test_state_revisions.py` upgrades a
database of an older revision holding old payloads. Alembic revisions
(`state/migrations/versions/`) are frozen snapshots, so `.flake8` lets them use literal names and
lengths (`WPS226`, `WPS432`) instead of app constants.

## Dependencies

- The worker's GPU stack (WhisperX, torch cu128 from the PyTorch index) is the worker's
  optional `engine` extra. `uv sync` and CI do not install it; worker tests use a fake engine.
  The Docker build installs it (`uv sync --package transcribe-worker --extra engine`).
- boto3 is typed through the `types-boto3[s3]` dev dependency (imported only under
  `TYPE_CHECKING`).
- CI installs the system `ffmpeg` for the worker and desktop targets (apt on Ubuntu,
  Chocolatey on Windows, retried up to three times and passed only once the `ffmpeg` and
  `ffprobe` shims run, since Chocolatey can exit 0 without installing; the worker's decode
  tests and the desktop's media tests run the real binary, the desktop's generating tiny media
  with `lavfi` sources in `apps/desktop/tests/support/media.py`); locally it must be on
  `PATH`. The generator uses only FFmpeg's own encoders (Opus through the native encoder with
  `-strict experimental`), so the same tests also run on the bundled LGPL build in
  `release-desktop.yml`; `apps/desktop/tests/fixtures/zlib_subtitles.mkv` (a zlib-compressed
  SubRip track, made with mkvmerge as noted in `support/media.py`) needs an ffmpeg with zlib,
  which every common build has. Kill-on-cancel and
  timeout through the media adapter are tested on every OS with a Python child standing in for
  ffmpeg (directly and behind a shim-like parent, `test_media_kill.py`); the same tests on the
  real ffmpeg read a named pipe and run on POSIX only.
- The desktop's runtime dependencies for all of M5 (pydantic, sqlalchemy, alembic,
  platformdirs, keyring, httpx, boto3) were added at once by M5.1, so parallel parts do not
  conflict on `uv.lock`. `keyring` and `platformdirs` ship their own types.
- The release build's tools are the desktop app's `packaging` dependency group (PyInstaller;
  Pillow for the icon, M8.3), installed only into the build environment
  (`uv sync --locked --no-dev --no-editable --package transcribe-ui --group packaging`), never
  with the dev tools, so no dev tool can reach a bundle. The dev group has only
  `types-pyinstaller` for the packaging files' types.
- The GUI uses `pyside6-essentials` (QtCore, QtGui, QtWidgets, QtTest; LGPL modules only) as a
  runtime dependency and `pytest-qt` as a dev dependency, both added by M6.2 for all of M6.
  mypy strict uses PySide6's own stubs; no override was needed. Only `gui/` imports Qt, and the
  CLI never imports `gui/` (tested). CI's Ubuntu desktop leg installs the libraries Qt loads
  even offscreen: `libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3` (apt); Windows
  needs nothing extra.
- Qt overrides keep Qt's names (`closeEvent`, `run`, `reject`, later `rowCount`, `data`,
  `index`) and carry `@typing.override`, which mypy checks and both ruff's and pep8-naming's
  N802 honour. WPS110 does not: `.flake8` ignores WPS110 for `*/gui/*/models.py`, the Qt
  item models (`data`, `index`, `setData`'s `value`; ruff needs none). An override returns
  `object` rather than `Any` (flake8-annotations' ANN401), and Qt's default parent index is a
  module constant (`TOP_LEVEL = QModelIndex()`, WPS404).
- pytest-qt imports QtGui, QtWidgets and QtTest when pytest starts, which needs the system
  libraries Qt loads. `scripts/check.py` therefore runs pytest with `-p no:pytest-qt` for every
  target except `desktop` (the only one CI installs them for); run other members' tests by
  hand the same way on a machine without them.
- mypy runs on the worker without the `engine` extra: `[[tool.mypy.overrides]]` sets
  `ignore_missing_imports` for `whisperx`, `torch`, `torchaudio`, `ctranslate2` and
  `faster_whisper`; only the engine adapter modules (`engine/whisperx_engine.py`,
  `engine/silero.py`, `engine/bake.py`) import them.
- Markers: `integration` (real R2/Runpod) and `gpu` (needs the GPU stack) are deselected by
  default and in CI; Runpod ones that create or change anything run only with the architect
  present (read-only calls may run any time).

## Translations

- Babel (a dev dependency only; the app reads the catalogs with the standard library) through
  `uv run scripts/catalogs.py update`: it extracts the texts marked with `tr` and `ntr:1,2`
  (no default keywords: `_` is the linters' name for unused values) from the files
  `apps/desktop/babel.cfg` maps into `transcribe_ui/locale/transcribe_ui.pot` (sorted by msgid,
  file names without line numbers, so moving code changes nothing), merges it into the `en`
  and `hu` catalogs (new texts empty, removed ones dropped, no fuzzy matching) and compiles the
  committed `.mo` files, so the app also runs from source. A file whose content would not
  change apart from its `POT-Creation-Date` is not rewritten. A new or changed text therefore
  means: run `update`, write the Hungarian text in `hu/LC_MESSAGES/transcribe_ui.po`, run
  `update` again (it compiles the `.mo`). `.gitattributes` marks `.mo` files binary.
- The desktop target of `scripts/check.py` runs `scripts/catalogs.py check`: it fails when
  `update` would change a file (the `.pot` equals a fresh extraction and each `.po` the merge
  of it, ignoring `POT-Creation-Date`; each `.mo` equals a fresh compile), when a Hungarian
  text is missing or fuzzy, or when a Hungarian text does not use exactly the `{fields}` of
  its English text, format specs included (`{cost:.2f}`, `{audio_s:duration}`).
- PyInstaller bundles the `.mo` files in both executables (data of the `transcribe_ui`
  package, `transcribe_ui/locale/**/*.mo`, through the project hook; not the `.po`/`.pot`) and,
  in the GUI executable, Qt's `qtbase_hu.qm` (Qt's translations folder; without it Qt's own
  buttons stay English). The release smoke test (`cli-help-hu`) runs the CLI's `--help` in
  Hungarian on every OS (Linux with `LANGUAGE=hu`, Windows and macOS after setting the
  `language` setting to `hu`) and looks for the Hungarian help of `--version` read from the
  repository's `hu` catalog; the GUI checks then run in Hungarian too, and the GUI's smoke mode
  fails when Qt's Hungarian translation did not load.

## Commits and branches

- Conventional Commits, enforced by gitlint (`.gitlint`, `contrib-title-conventional-commits`)
  in CI on PR commits; body lines at most 80 characters (wrap at 72). No co-author or
  attribution trailers, no "Generated with" line in PR descriptions. A pushed commit that
  fails gitlint cannot be fixed by force push: recreate the branch under a new name.
- `main` is protected: PR required, signed commits required, no force push, required status
  checks (added once `ci.yml` exists). Work happens on branches; each PR is reviewed by a
  `code-reviewer` sub-agent before merge.
- PRs are squash-merged only. PR titles stay at most 65 characters so GitHub's " (#N)"
  suffix keeps the squash title within 72.

## Claude Code hook

`.claude/settings.json` registers `.claude/hooks/review_gate.py` (stdlib only, run via
`uv run --no-project`):

- `UserPromptSubmit` stores a fingerprint of HEAD and all changed/untracked files.
- `Stop` compares; if anything changed this turn and that change-set was not reviewed yet, it
  returns `additionalContext` that makes Claude run the `code-reviewer` sub-agent
  (`.claude/agents/code-reviewer.md`, read-only tools) and address high/medium findings.
- `stop_hook_active` and the stored "reviewed" fingerprint prevent loops. State is kept under
  the OS temp dir, never in the repo. Any hook failure lets Claude stop normally.
- New agent definitions are loaded at session start; after editing `.claude/agents/`, restart
  the session.

## CI/CD

`ci.yml`, `release-worker.yml`, `reaper.yml` and `release-desktop.yml` exist;
`release-desktop.yml` publishes nothing until M8.5 adds its release job.

| Workflow | Trigger | Jobs |
|---|---|---|
| `ci.yml` | every pull request and push to `main` (no workflow-level path filter) | `changes` (dorny/paths-filter; a shared-config change selects every target; a `jq` step turns the changed targets into matrix legs) → `check` matrix running `scripts/check.py <target>`, every target on `ubuntu-24.04` and the desktop target also on `windows-2025`; `gitlint` on PR commits; aggregate `ci-ok` (fails unless every needed job succeeded or was skipped) = the only required check (it covers `gitlint`) |
| `release-desktop.yml` | pull requests and pushes to `main` touching `apps/desktop/**` (not `apps/desktop/tests/**`, except `test_media_ffmpeg.py`, `test_batches_ffprobe.py`, `support/media.py`, `conftest.py`, `fixtures/zlib_subtitles.mkv`), `packages/contracts/**`, `uv.lock`, `pyproject.toml`, `.python-version`, `scripts/build_desktop*.py`, `scripts/build_ffmpeg*.py`, `scripts/smoke_desktop_*.py`, `scripts/release_desktop.py` or the workflow; manual dispatch | `ffmpeg` matrix, one leg per platform (fail-fast off, 60 minutes, checkout without persisted credentials; Windows cross-built on `ubuntu-24.04` with Ubuntu's `gcc-mingw-w64-x86-64-win32` and `nasm`, macOS on `macos-15`/`macos-15-intel` with Homebrew's `nasm` on Intel, Linux in the manylinux container as root with `nasm` from CRB and `pkgconf-pkg-config`, run by the image's CPython 3.13 without writing bytecode, the files handed back to the runner's user): `build_ffmpeg.py pins` (the cache key, the build image and the packages to install, all pinned in `build_ffmpeg_pins.py`), `actions/cache/restore@v6` with `lookup-only` (`enableCrossOsArchive` for Windows), and only on a miss `build_ffmpeg.py build` (into `build/ffmpeg/dist`) and `actions/cache/save@v6`. Then the `build` matrix (`needs: ffmpeg`: a failed ffmpeg build of one platform skips all four build legs, accepted as a release gate, since a release needs all eight files anyway), one leg per platform (fail-fast off, 75 minutes, `contents: read`): Windows x64 (`windows-2025`), macOS arm64 (`macos-15`), macOS x86_64 (`macos-15-intel`), Linux x64 (`ubuntu-24.04`, the build inside `quay.io/pypa/manylinux_2_34_x86_64` pinned by digest in `build_ffmpeg_pins.py` and read through `build_ffmpeg.py pins`, run as the runner's user with the runner's uv binary and cache). `UV_PYTHON_PREFERENCE=only-managed` for every leg and the container, so only uv's own CPython builds (a Homebrew Python built for a newer macOS, or the manylinux image's static ones, would be found otherwise); checkout without persisted credentials, actions pinned by SHA. Each leg: the ffmpeg entry restored (`fail-on-cache-miss`), `build_ffmpeg.py verify --first-on-path` (`-buildconf`, `-L`, the built programs first on `PATH`), `test_media_ffmpeg.py` and `test_batches_ffprobe.py` with the built programs first on `PATH` in a second environment with the dev group (`UV_PROJECT_ENVIRONMENT` in the runner's temp folder, `-p no:pytest-qt`), `build_desktop.py stamp` (version and tag from the commit), the build environment (`uv sync --locked --no-dev --no-editable --package transcribe-ui --group packaging`), `build_desktop.py build --ffmpeg-dir build/ffmpeg/dist` (both executables into `dist/`, each bundling ffmpeg and ffprobe; it prints the build interpreter, and on macOS checks the floor of 14: a wheel's binaries by the wheel's `macosx` tag (from its `WHEEL` file, the binary found through the wheels' `RECORD` files), every other binary (the bundled ffmpeg and ffprobe among them) and the built executable itself (the bootloader) by its `minos` from `otool -arch all -l`; one line per wheel shows its tags and highest `minos`), on Linux the libraries of `apps/desktop/packaging/linux-packages.txt` plus Xvfb, `smoke_desktop_build.py` (the checks of architecture §8; on Linux also `linux-floor` in `ubuntu:22.04` pinned by digest; a summary with sizes and the CLI's cold start in the job summary), then the two executables with `actions/upload-artifact@v7` `archive: false` (7 days). uv's cache through `setup-uv` per OS. No release job yet (M8.5: `release-check`, then `publish` in the `desktop-release` environment) |
| `release-worker.yml` | pull requests and pushes to `main` touching `apps/worker/**`, `packages/contracts/**`, `uv.lock`, `pyproject.toml`, `.python-version`, `.dockerignore`, `scripts/smoke_worker_*.py`, `scripts/smoke_fixtures.py` or the workflow; manual dispatch | `build`: free runner disk, `docker buildx build -f apps/worker/Dockerfile`, `scripts/smoke_worker_image.py` (moto S3 in Docker, fake Runpod API on localhost, the image run as a pod with `TRANSCRIBE_ENGINE=fake`: result, `_done.json`, finished status, pod deleted, exit 0; then, inside the image, the CUDA libraries load and, with `--network none`, the GPU stack imports, the Whisper model resolves with `local_files_only` as the engine loads it, the baked hu/en aligners load, and WhisperX's Silero VAD is built from the baked copy and run on silence). On `main` only: the image is saved (`docker save` + zstd) as a one-day artifact (the repository is public, so artifact storage is free), and a separate `push` job in `environment: dockerhub` loads it and pushes `sha-<short>`, `schema-<major>` and `latest` to `markfazekasgh/transcribe-ui-worker`. The job holding the token never runs the image. |
| `reaper.yml` | schedule (`*/15 * * * *`); manual dispatch with a `dry-run` input | `reap` in `environment: runpod-reaper`: without the secret only a `::warning::` and success; otherwise uv pinned to 0.11.8, `uv sync --locked --no-dev --package transcribe-worker` (no `engine` extra, no uv cache) and `transcribe-worker-reaper [--dry-run]`; concurrency groups `reaper-live` / `reaper-dry` (never cancelled; a dry run cannot replace a pending live pass), 15-minute timeout; checkout pinned by SHA |

Nothing is published from a pull request (`release-worker.yml` only builds and smoke-tests
there). Concurrency groups cancel superseded pull request runs; a running workflow on `main` is not
cancelled (a newer push can still replace one that is only queued).

Deployment credentials live in GitHub environments whose deployment branch policy allows only
`main`, so jobs on other branches (even with an edited workflow) never receive them:

| Environment | Variables | Secrets | Used by |
|---|---|---|---|
| `dockerhub` | `DOCKERHUB_USERNAME` | `DOCKERHUB_TOKEN` (Docker Hub personal access token, Read & Write) | `release-worker.yml` push job |
| `runpod-reaper` | | `RUNPOD_REAPER_API_KEY` (Runpod key, scope Pods read+write only) | `reaper.yml` |

Set them from the repo root with `gh variable set NAME --env <env> --body <value>` and
`gh secret set NAME --env <env>` (prompts for the value, so it never lands in shell history;
run it in a separate terminal: the Claude Code `!` prefix cannot answer the prompt and stores
an empty secret).

PyInstaller notes (`scripts/build_desktop.py`, `apps/desktop/packaging/`):

- Options for both executables: `--onefile --noupx --clean`, `--copy-metadata transcribe-ui`,
  `--copy-metadata keyring` and `--collect-submodules keyring.backends`, the stamp as
  `--add-data <work>/version.txt:transcribe_ui`, the project hooks with
  `--additional-hooks-dir`, ffmpeg and ffprobe of `--ffmpeg-dir` with `--add-binary <file>:.`
  (the archive's root, where `media/tools.py` `bundle_dir` looks). The CLI excludes `PySide6`, `shiboken6` and `transcribe_ui.gui`;
  the GUI excludes `PySide6.QtTest` and is `--windowed` on Windows (with a generated
  `--version-file`: product name, file description, numeric file version, the stamp as product
  version, original file name). PyInstaller writes its spec into the work folder (`build/desktop/`).
- `hook-transcribe_ui.py` collects every submodule of `transcribe_ui` (the GUI entry imports
  `gui.app` through `importlib`; the Alembic environment and revisions are analysed for their
  imports), leaving `transcribe_ui.gui` out of the CLI (`TRANSCRIBE_UI_BUNDLE_VARIANT`, set by
  the build script), and the data: `locale/**/*.mo` and `state/migrations` with its `.py` files.
- PyInstaller has no command-line option to leave out a shared library its analysis finds;
  `packaging/pyinstaller_main.py` (the build runs PyInstaller through it) adds `libstdc++` and
  `libgcc_s` to its exclude list on Linux, so the host's newer copies (which Mesa's LLVM needs)
  are never shadowed. `cli-bundle`/`gui-bundle` fail if one is bundled.
- `packaging/entry_gui.py` imports the app inside its guard and catches an exception that
  escapes the GUI (a failing import included): it prints it to stderr where there is one, logs
  it to the GUI log when the app's logging can still be imported, and exits 1: the windowed Windows bootloader would
  otherwise show a modal traceback dialog, and a smoke run would wait for its timeout.
- The smoke mode of the GUI (architecture §3.1) also fails on any record at ERROR or above in
  the app's loggers while it watches (an exception escaping a Qt slot, a critical message of Qt).
- Linux build tools: PyInstaller runs `ldd`, `objdump` and `objcopy` (the manylinux image has
  them). Libraries the build container lacks (the X11/xcb, Wayland, GL and font stack) are not
  bundled; `linux-packages.txt` lists them for the host.
- Build locally (Linux, macOS or Windows; from the repo root, with
  `UV_PYTHON_PREFERENCE=only-managed` exported as in CI, else a pyenv or system Python may be
  used): first ffmpeg, once per platform, with
  `uv run --no-project scripts/build_ffmpeg.py build` (into `build/ffmpeg/dist`, which
  `build_desktop.py build` bundles by default; Linux needs a C compiler, `make`, `nasm` and
  `pkg-config`, macOS the Xcode command line tools (and `nasm` on Intel); on Windows
  cross-build it on Linux with `--platform windows-x64`), then
  `UV_PROJECT_ENVIRONMENT=<folder> uv sync --locked --no-dev --no-editable --package transcribe-ui --group packaging --reinstall-package transcribe-ui`
  (the reinstall picks up source changes: uv rebuilds a non-editable local package only when
  its `pyproject.toml` changes), then with the same variable
  `uv run --no-sync python scripts/build_desktop.py build --version "$(uv run --no-project scripts/build_desktop.py stamp | sed -n 's/^version=//p')"`
  and `uv run --no-sync python scripts/smoke_desktop_build.py dist/<cli> dist/<gui> --version <same> --skip linux-floor`
  (`--skip gui-native` without Xvfb).

ffmpeg build notes (`scripts/build_ffmpeg.py`, `build_ffmpeg_pins.py`, `build_ffmpeg_steps.py`,
`build_ffmpeg_checks.py`; standard library only):

- `build_ffmpeg_pins.py` holds the pinned FFmpeg and zlib sources (version, URL, SHA-256, the
  signing key's fingerprint, the licence file) and each platform's configure line; the notices
  (M8.3) and the release notes (M8.5) read them from there (`build_info`) or from
  `build_ffmpeg.py configuration` (JSON for all four platforms). A build also writes
  `ffmpeg-build.json` and the licence texts (`licences/`) next to the programs.
- A version bump: download the new tarball and its `.asc`, check the signature with the key
  of the pinned fingerprint, and change the version, URL and SHA-256 together (plus
  `research/desktop-release.md`). The cache key hashes every `scripts/build_ffmpeg*.py`,
  `build_ffmpeg_pins.py` among them with the inputs the workflow reads through
  `build_ffmpeg.py pins` (`CACHE_SALT`, the manylinux image, `BUILD_PACKAGES`), so any change
  there rebuilds all four platforms on the next run. The workflow's own commands are not
  hashed: a change to the `ffmpeg` job's install or build commands (the `apt`, `brew` or `dnf`
  flags, the docker `--env` list) needs a `CACHE_SALT` bump. Neither are the runners' own
  compilers and SDKs: a cached binary may predate the runner's current compiler, and bumping a
  pin or `CACHE_SALT` forces a rebuild.
- `build` runs only where it can (the Windows build on Linux, every other platform on itself)
  and refuses an `--out` folder that holds files but no `ffmpeg-build.json`, since it replaces
  that folder. It downloads with `curl` (three retries), refuses a tarball whose SHA-256
  differs, builds out of tree in `build/ffmpeg/work`, and points `PKG_CONFIG_LIBDIR` at the
  static zlib (an empty folder on macOS), so no library of the build machine is picked up. It
  fails on any configure result or linked library outside the allow-list; a check program that
  runs longer than 60 s is a failure, not a hang.

PyInstaller 6.10 or later:
the GUI's restart after a language change (architecture §3.1 "Settings") starts the one-file
executable again with `PYINSTALLER_RESET_ENVIRONMENT=1`, which earlier versions ignore (the
new process would reuse the unpacked files the exiting one removes). M8 manual check on each
OS, Windows first: change the language in Settings, choose Restart now, and the new window
opens in the new language and comes to the foreground (Windows may only flash it in the task
bar, since the starting process has exited).
