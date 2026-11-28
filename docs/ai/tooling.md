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
gain.

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
runs as `uv run transcribe-ui ...` (`schema`, `run <request.json>`, `--version`) after
`uv sync --all-packages`.

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
  `PATH`. Kill-on-cancel and
  timeout through the media adapter are tested on every OS with a Python child standing in for
  ffmpeg (directly and behind a shim-like parent, `test_media_kill.py`); the same tests on the
  real ffmpeg read a named pipe and run on POSIX only.
- The desktop's runtime dependencies for all of M5 (pydantic, sqlalchemy, alembic,
  platformdirs, keyring, httpx, boto3) were added at once by M5.1, so parallel parts do not
  conflict on `uv.lock`. `keyring` and `platformdirs` ship their own types.
- The GUI uses `pyside6-essentials` (QtCore, QtGui, QtWidgets, QtTest; LGPL modules only) as a
  runtime dependency and `pytest-qt` as a dev dependency, both added by M6.2 for all of M6.
  mypy strict uses PySide6's own stubs; no override was needed. Only `gui/` imports Qt, and the
  CLI never imports `gui/` (tested). CI's Ubuntu desktop leg installs the libraries Qt loads
  even offscreen: `libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3` (apt); Windows
  needs nothing extra.
- Qt overrides keep Qt's names (`closeEvent`, `run`, `reject`, later `rowCount`, `data`,
  `index`) and carry `@typing.override`, which mypy checks and both ruff's and pep8-naming's
  N802 honour. WPS110 does not: the part that adds the first item model or view overriding
  `data`/`index` adds a `.flake8` per-file ignore of WPS110 for `*/gui/*/models.py` and
  `*/gui/*/view.py` (ruff needs none).
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
- PyInstaller (M8) bundles the `.mo` files in both executables (data of the `transcribe_ui`
  package, `transcribe_ui/locale/**/*.mo`) and, in the GUI executable, Qt's `qtbase_hu.qm`
  (Qt's translations folder; without it Qt's own buttons stay English); the release smoke test
  on Linux runs the CLI's `--help` with `LANGUAGE=hu` and checks for a Hungarian line from the
  app's own help texts.

## Commits and branches

- Conventional Commits, enforced by gitlint (`.gitlint`, `contrib-title-conventional-commits`)
  in CI on PR commits; body lines at most 80 characters (wrap at 72). No co-author or
  attribution trailers, no "Generated with" line in PR descriptions. A pushed commit that
  fails gitlint cannot be fixed by force push: recreate the branch under a new name.
- `main` is protected: PR required, signed commits required, no force push, required status
  checks (added once `ci.yml` exists). Work happens on branches; each PR is reviewed by a
  `code-reviewer` sub-agent before merge.

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

`ci.yml`, `release-worker.yml` and `reaper.yml` exist; `release-desktop.yml` is added with the
desktop app.

| Workflow | Trigger | Jobs |
|---|---|---|
| `ci.yml` | every pull request and push to `main` (no workflow-level path filter) | `changes` (dorny/paths-filter; a shared-config change selects every target; a `jq` step turns the changed targets into matrix legs) → `check` matrix running `scripts/check.py <target>`, every target on `ubuntu-24.04` and the desktop target also on `windows-2025`; `gitlint` on PR commits; aggregate `ci-ok` (fails unless every needed job succeeded or was skipped) = the only required check (it covers `gitlint`) |
| `release-desktop.yml` | push to `main`, paths `apps/desktop/**`, `packages/contracts/**`, `uv.lock` | PyInstaller one-file builds, two per OS (the GUI executable `transcribe-ui-gui` with Qt and the CLI executable `transcribe-ui` without Qt), on Windows x64, macOS arm64 (`macos-15`), macOS x86_64 (`macos-15-intel`), Linux x64 (`ubuntu-22.04`); smoke tests of both builds on their own OS: the CLI with `--version`, the GUI with a start-and-quit check (started with `QT_QPA_PLATFORM=offscreen`, it must start and quit by itself with exit code 0, on every platform); upload only the executables with `actions/upload-artifact@v7` `archive: false` (file names unique per OS and variant, e.g. `transcribe-ui-linux-x64-gui`, `transcribe-ui-linux-x64-cli`); then a release job (`needs:` all four build legs, `permissions: contents: write`) creates a GitHub Release tagged `desktop-<YYYYMMDD>-<short sha>` with the eight executables, only if every build and smoke test passed |
| `release-worker.yml` | pull requests and pushes to `main` touching `apps/worker/**`, `packages/contracts/**`, `uv.lock`, `pyproject.toml`, `.python-version`, `.dockerignore`, `scripts/smoke_*.py` or the workflow; manual dispatch | `build`: free runner disk, `docker buildx build -f apps/worker/Dockerfile`, `scripts/smoke_worker_image.py` (moto S3 in Docker, fake Runpod API on localhost, the image run as a pod with `TRANSCRIBE_ENGINE=fake`: result, `_done.json`, finished status, pod deleted, exit 0; then, inside the image, the CUDA libraries load and, with `--network none`, the GPU stack imports, the Whisper model resolves with `local_files_only` as the engine loads it, the baked hu/en aligners load, and WhisperX's Silero VAD is built from the baked copy and run on silence). On `main` only: the image is saved (`docker save` + zstd) as a one-day artifact (the repository is public, so artifact storage is free), and a separate `push` job in `environment: dockerhub` loads it and pushes `sha-<short>`, `schema-<major>` and `latest` to `markfazekasgh/transcribe-ui-worker`. The job holding the token never runs the image. |
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

PyInstaller notes: disable UPX; for keyring add `--copy-metadata keyring` and
`--collect-submodules keyring.backends`; exclude unused Qt modules.
