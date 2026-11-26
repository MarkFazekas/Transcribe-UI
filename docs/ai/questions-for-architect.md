# Questions for the architect

Answer each with "default" to accept the proposed default, or with a different decision.
Answered questions move to "Decided" with the decision; the ID stays stable.
Background for most items: `docs/ai/architecture.md` and `docs/ai/research/`.

## Open

**A1. One batch per selection instead of one batch per language?**
The brief assumes Whisper needs per-language batches. It does not: `language` is a per-call
decode parameter, and one loaded model handles files of different languages back to back
(`research/whisper.md` §3). Per-language batches would upload the same audio once per
language (or need shared objects with reference counting for cleanup).
Default: one batch per selection; each item lists its target languages; audio uploaded once,
transcribed once per language. Batch id stays `batch_YYYYMMDD_HHmmss`.

**A2. Engine: faster-whisper directly instead of WhisperX?**
STTCLI used WhisperX 3.7.9, whose decoder is faster-whisper anyway. Using faster-whisper
directly drops torch, pyannote and per-language wav2vec2 aligners (smaller image, no torch pin
churn, no aligner reload per language). Cost: no wav2vec2 forced alignment of word timestamps.
Default: faster-whisper, `large-v3` fp16, batched (16) with Silero VAD, sequential re-run with
hallucination guards for suspicious files. WhisperX alignment can be added as an optional stage
if subtitle timing proves too loose on real material.

**A3. Worker base image?**
Of the four suggested tags, only CUDA 12.x is usable: CTranslate2 has no CUDA 13 wheels, and a
CUDA 13 floor shrinks the A5000 host pool. Torch is not needed with A2.
Default: `nvidia/cuda:12.8.x-cudnn-runtime-ubuntu24.04` + Python 3.13 + faster-whisper + ffmpeg
+ openssh-server, model baked in (much smaller pull than the 10.65 GB runpod/pytorch image;
the pull is billed on every run). Alternative: `runpod/pytorch:2.1.0-cu1281-torch280-ubuntu2404`
(Runpod `/start.sh` conveniences, larger).

**A4. Runpod API v2 via a thin `httpx` client?**
REST v1 (rest.runpod.io/v1) was retired on 2026-11-15; GraphQL retires in early 2027; the
Python SDK 2.0 focuses on Flash. "Deploy when available" is the `queue` object of
`POST /v2/pods` (Secure cloud only).
Default: own typed client for the handful of v2 endpoints, Secure cloud, `queue` with
`expiresAt` (product Q17), `minCudaVersion: "12.8"` rather than an exact version list.

**A5. Is the "1 GB Global Volume persistent disk" the Runpod Global Volume (beta)?**
Global Volumes are not tied to a datacenter (so they do not narrow A5000 availability), survive
terminate, have no fixed size (billed by usage plus requests) and have object-store semantics
(no locking, no atomic rename). A network volume would pin the pod to one datacenter; a pod
volume disk is deleted on terminate and has a 10 GB minimum in API v2.
Default: Global Volume, mounted at `/workspace`, used only for write-once result backups (and
optionally model caches). R2 remains the primary store.

**A6. Test mode and the "never leave the pod running" rule?**
Default: `KEEP_POD_ALIVE=true` skips the final self-terminate so you can SSH in, but the
watchdog (a separate stdlib-only process, independent of the worker) still terminates after
`MAX_RUNTIME_S` (default 4 h in test mode; in production derived from the total audio duration
with a margin). The desktop app and the external reaper (A17) enforce their deadlines too. Is a
hard cap acceptable during testing, or do you want an "unlimited" switch?

**A7. How does the pod get its R2 credentials?**
Default: a dedicated R2 API token scoped to the single bucket (R2 cannot scope by prefix),
stored as Runpod Secrets and referenced from a Runpod template that the desktop app creates and
updates through the API. The image and pod-create request never contain the key.
Later hardening option: presigned URLs in a per-run job object (no long-lived key in the pod).

**A8. Python 3.13 for all workspace members?**
3.14 works for PySide6 6.11+ and PyInstaller 6.15+, but CTranslate2/PyAV support on 3.14 is not
confirmed. Default: 3.13 everywhere.

**A9. Desktop stack: PySide6 + PyInstaller one-file, ffmpeg bundled?**
Default:
- PySide6 (Qt Widgets, LGPL; Qt libraries are extracted as separate files, which keeps LGPL
  relinking possible; licence texts shipped).
- PyInstaller `--onefile` (Nuitka is slower to build and less proven with PySide6 on 3.13+).
- Static LGPL ffmpeg/ffprobe builds embedded per OS (exe grows to roughly 150–300 MB), with a
  config/`PATH` override.
- Builds: Windows x64, macOS arm64, macOS x86_64, Linux x64 built on Ubuntu 22.04 (glibc
  baseline). Unsigned for now: macOS users must clear the quarantine flag, and Windows
  SmartScreen may warn. Code signing would need certificates.

**A10. Local batch state in SQLite?**
Default: one SQLite database (WAL) in the OS user data dir, Pydantic models stored as JSON with
indexed status columns, single-instance lock. JSON files per batch were considered; SQLite gives
atomic multi-row transitions for tens of thousands of items.

**A11. Repository layout as a uv workspace?**
Default: `packages/contracts` (shared Pydantic models: R2 layout, manifest, results),
`apps/desktop`, `apps/worker`; one root `uv.lock`; tool config at the root.

**A12. Docker Hub repository and image tags?**
Needed: the Docker Hub namespace/repository name, and a Docker Hub access token stored as the
GitHub secrets `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN`.
Default tags: `sha-<short>`, `latest` and `schema-<contracts major>`. The desktop app uses the
`schema-<major>` tag of the contracts it was built with. (Pinning `sha-` would break when a
desktop-only change does not rebuild the image.)

**A13. CI scope details?**
Default:
- `ci.yml` always runs (no workflow-level path filter, which would leave required checks
  pending forever); jobs per member are skipped when unchanged; one aggregate `ci-ok` job plus
  gitlint are the required status checks on `main`.
- Desktop tests run on Ubuntu and Windows (path handling differs); macOS only builds.
- Release artifacts are only the bare executables (`upload-artifact` with `archive: false`,
  90-day retention). No GitHub Release objects unless you want them.

**A14. Lint adjustments to the reference config?**
Applied, please confirm:
- `W503` added to the flake8 ignore list: setting `ignore=` drops flake8's default ignores, and
  W503 contradicts `ruff format`.
- ruff mccabe `max-complexity` 10 → 8 to match flake8 (flake8 enforced 8 anyway).
- `dictionaries` without `pandas`; new empty `whitelist.txt`.
- `E704` and `E701` added to the flake8 ignore list: `ruff format` writes Protocol/overload
  stubs as `def f(self) -> int: ...` (verified with a probe).
- `exclude` added to `.flake8` (it scanned `.venv`).
- pytest `--import-mode=importlib` (several members have `tests/` dirs).
- Wemake limits (for example `max-methods=10`) stay as they are. Qt model classes may need
  justified per-class `noqa` comments.

**A15. Real-infrastructure checks with your keys?**
Several Runpod facts cannot be verified from docs and decide parts of the design: whether the
pod-scoped `RUNPOD_API_KEY` may delete its own pod, whether `queue` is allowed on your plan
together with a Global Volume, whether `{{ RUNPOD_SECRET_x }}` works in direct-create `env`,
and which CUDA versions the A5000 hosts report. Default: once a Runpod key and an R2 test bucket
are in `.env`, I run these checks with a minimal pod (a few cents) and record the results.
Integration tests against real R2/Runpod are marked and not part of the default CI run.

**A16. Review gate hook as implemented?**
`.claude/hooks/review_gate.py` fingerprints HEAD plus changed files at each prompt; at Stop it
asks for a `code-reviewer` sub-agent review once per change-set (`stop_hook_active` prevents
loops). It runs with `uv run --no-project`, so it works on Windows without `python3`.
Default: keep as is.

**A17. External reaper for forgotten pods?**
A queued pod can start while the desktop app is closed, and failures before Python starts
(image pull error, crash loop) bypass every in-pod safeguard.
Default: a scheduled GitHub Actions workflow (every 15 min) with a separate pods-only Runpod key
in a repository secret terminates any `transcribe-ui-*` pod older than its deadline (the
deadline is encoded in the pod name or env at creation). Cost: none beyond Actions minutes.

## Decided

(none yet)
