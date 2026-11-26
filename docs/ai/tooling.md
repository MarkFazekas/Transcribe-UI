# Tooling

## Environment

- uv manages Python (3.13, `.python-version`) and the workspace. `uv sync` installs every
  member plus the root `dev` dependency group.
- Credentials for local integration work live in an uncommitted `.env` (see `.gitignore`);
  CI uses GitHub repository/environment secrets.

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
- mypy runs on the worker without the `engine` extra: `[[tool.mypy.overrides]]` sets
  `ignore_missing_imports` for `whisperx`, `torch`, `torchaudio` and `ctranslate2`, and the
  engine adapter is the only module importing them.
- Markers: `integration` (real R2/Runpod) and `gpu` (needs the GPU stack) are deselected by
  default and in CI; Runpod ones run only with the architect present.

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

`ci.yml` exists; the release and reaper workflows are added with the apps they ship.

| Workflow | Trigger | Jobs |
|---|---|---|
| `ci.yml` | every pull request and push to `main` (no workflow-level path filter) | `changes` (dorny/paths-filter; a shared-config change selects every target) → `check` matrix running `scripts/check.py <target>` (desktop also on Windows once it exists); `gitlint` on PR commits; aggregate `ci-ok` (fails unless every needed job succeeded or was skipped) = the only required check (it covers `gitlint`) |
| `release-desktop.yml` | push to `main`, paths `apps/desktop/**`, `packages/contracts/**`, `uv.lock` | PyInstaller one-file on Windows x64, macOS arm64 (`macos-15`), macOS x86_64 (`macos-15-intel`), Linux x64 (`ubuntu-22.04`); smoke `--version`; upload only the executable with `actions/upload-artifact@v7` `archive: false` (unique file name per OS); then a release job (`needs:` all four builds, `permissions: contents: write`) creates a GitHub Release tagged `desktop-<YYYYMMDD>-<short sha>` with the four executables, only if every build and smoke test passed |
| `release-worker.yml` | push to `main`, paths `apps/worker/**`, `packages/contracts/**`, `uv.lock` | free runner disk, build the Docker image, run the entry/termination smoke test inside it, push (`sha-<short>`, `latest`, `schema-<major>`; push skipped until the Docker Hub secrets exist, open question A12) |
| `reaper.yml` | schedule, every 15 min | terminate overdue `transcribe-ui-*` pods with a pods-only key |

Releases never run on pull requests. Concurrency groups cancel superseded runs.

PyInstaller notes: disable UPX; for keyring add `--copy-metadata keyring` and
`--collect-submodules keyring.backends`; exclude unused Qt modules.
