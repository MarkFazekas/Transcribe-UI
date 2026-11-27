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
`UPDATE_SNAPSHOTS=1 uv run pytest packages/contracts/tests`; review the diff.

## Dependencies

- The worker's GPU stack (WhisperX, torch cu128 from the PyTorch index) is the worker's
  optional `engine` extra. `uv sync` and CI do not install it; worker tests use a fake engine.
  The Docker build installs it (`uv sync --package transcribe-worker --extra engine`).
- boto3 is typed through the `types-boto3[s3]` dev dependency (imported only under
  `TYPE_CHECKING`).
- CI installs the system `ffmpeg` for the worker target (the decode tests run the real
  binary); locally it must be on `PATH`.
- mypy runs on the worker without the `engine` extra: `[[tool.mypy.overrides]]` sets
  `ignore_missing_imports` for `whisperx`, `torch`, `torchaudio` and `ctranslate2`, and the
  engine adapter is the only module importing them.
- Markers: `integration` (real R2/Runpod) and `gpu` (needs the GPU stack) are deselected by
  default and in CI; Runpod ones that create or change anything run only with the architect
  present (read-only calls may run any time).

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

`ci.yml` and `release-worker.yml` exist; the other release and reaper workflows are added with
the apps they ship.

| Workflow | Trigger | Jobs |
|---|---|---|
| `ci.yml` | every pull request and push to `main` (no workflow-level path filter) | `changes` (dorny/paths-filter; a shared-config change selects every target) → `check` matrix running `scripts/check.py <target>` (desktop also on Windows once it exists); `gitlint` on PR commits; aggregate `ci-ok` (fails unless every needed job succeeded or was skipped) = the only required check (it covers `gitlint`) |
| `release-desktop.yml` | push to `main`, paths `apps/desktop/**`, `packages/contracts/**`, `uv.lock` | PyInstaller one-file on Windows x64, macOS arm64 (`macos-15`), macOS x86_64 (`macos-15-intel`), Linux x64 (`ubuntu-22.04`); smoke `--version`; upload only the executable with `actions/upload-artifact@v7` `archive: false` (unique file name per OS); then a release job (`needs:` all four builds, `permissions: contents: write`) creates a GitHub Release tagged `desktop-<YYYYMMDD>-<short sha>` with the four executables, only if every build and smoke test passed |
| `release-worker.yml` | pull requests and pushes to `main` touching `apps/worker/**`, `packages/contracts/**`, `uv.lock`, `pyproject.toml`, `.python-version`, `.dockerignore`, `scripts/smoke_*.py` or the workflow; manual dispatch | `build`: free runner disk, `docker buildx build -f apps/worker/Dockerfile`, `scripts/smoke_worker_image.py` (moto S3 in Docker, fake Runpod API on localhost, the image run as a pod with `TRANSCRIBE_ENGINE=fake`: result, `_done.json`, finished status, pod deleted, exit 0; then, inside the image, the CUDA libraries load and, without network, every baked model resolves). On `main` only: the image is saved (`docker save` + zstd) as a one-day artifact (the repository is public, so artifact storage is free), and a separate `push` job in `environment: dockerhub` loads it and pushes `sha-<short>`, `schema-<major>` and `latest` to `markfazekasgh/transcribe-ui-worker`. The job holding the token never runs the image. |
| `reaper.yml` | schedule, every 15 min | terminate overdue `transcribe-ui-*` pods with a pods-only key (`environment: runpod-reaper`) |

Nothing is published from a pull request (`release-worker.yml` only builds and smoke-tests
there). Concurrency groups cancel superseded pull request runs; a running workflow on `main` is not
cancelled (a newer push can still replace one that is only queued).

Deployment credentials live in GitHub environments whose deployment branch policy allows only
`main`, so jobs on other branches (even with an edited workflow) never receive them:

| Environment | Variables | Secrets | Used by |
|---|---|---|---|
| `dockerhub` | `DOCKERHUB_USERNAME` | `DOCKERHUB_TOKEN` (Docker Hub personal access token, Read & Write) | `release-worker.yml` push job |
| `runpod-reaper` (created with the reaper) | | pods-only Runpod key | `reaper.yml` |

Set them from the repo root with `gh variable set NAME --env <env> --body <value>` and
`gh secret set NAME --env <env>` (prompts for the value, so it never lands in shell history;
run it in a separate terminal: the Claude Code `!` prefix cannot answer the prompt and stores
an empty secret).

PyInstaller notes: disable UPX; for keyring add `--copy-metadata keyring` and
`--collect-submodules keyring.backends`; exclude unused Qt modules.
