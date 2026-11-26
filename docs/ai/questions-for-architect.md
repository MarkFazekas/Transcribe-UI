# Questions for the architect

Answer each with "default" to accept the proposed default, or with a different decision.
Answered questions move to "Decided" with the decision; the ID stays stable.
Background for most items: `docs/ai/architecture.md` and `docs/ai/research/`.

## Open

**A12. Docker Hub repository and image tags?**
Needed: the Docker Hub namespace/repository name, and a Docker Hub access token stored as the
GitHub secrets `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN`.
Default tags: `sha-<short>`, `latest` and `schema-<contracts major>`. The desktop app uses the
`schema-<major>` tag of the contracts it was built with. (Pinning `sha-` would break when a
desktop-only change does not rebuild the image.)
Assumption until answered (mine, not the architect's): `release-worker.yml` builds and
smoke-tests the image but skips the push, so no `schema-<major>` tag exists yet and the desktop
app cannot launch a worker.

## Decided

**A1. Batches and languages.** One batch per selection; each item lists its target languages
and its audio is uploaded once. The worker processes jobs language by language (all `en` jobs of
the run, then all `hu` jobs, …), and the prefetch queue follows the same order (architect's
answer: switching language reloads a per-language component, so en, en, hu is faster than
en, hu, en). Note: the per-language component is the WhisperX alignment model (A2); the Whisper
model itself is language-independent.

**A2. Engine.** WhisperX (latest 3.8.x) with wav2vec2 forced alignment, because output quality
comes first. `large-v3`, fp16, batch 16. Languages without an alignment model are transcribed
without alignment; the result records `aligned: false` and the language settings mark them.

**A3. Worker base image.** No sshd: SSH goes through Runpod's proxied basic SSH, which the docs
state is available on every pod (not verified yet; checked at the first approved run, A15). Torch is needed again (A2), so
the image is `nvidia/cuda:12.8.1-base-ubuntu24.04` + uv-managed Python 3.13 + the locked
`torch~=2.8.0` (cu128) / WhisperX stack. CUDA user-space libraries (cuBLAS, cuDNN 9) come from the
torch wheels and are put on `LD_LIBRARY_PATH` for CTranslate2. `runpod/pytorch:2.1.0-cu1281-
torch280-ubuntu2404` matches the torch pin, but its system Python and torch sit outside
`uv.lock`, and its extras (Jupyter, sshd, `/start.sh`) are not needed. cu1300/cu1320 are out:
CTranslate2 has no CUDA 13 wheels.

**A4. Runpod API.** Default: own typed `httpx` client for REST v2, Secure cloud, `queue` with
`expiresAt`, `minCudaVersion: "12.8"`.

**A5. Storage.** Global Volume at `/workspace`, used only for write-once result backups. Model
weights (Whisper `large-v3`, Silero VAD, alignment models for `hu` and `en`) are baked into the
image for a fast start; alignment models for other enabled languages are downloaded at run
time to the container disk.

**A6. Test mode.** `KEEP_POD_ALIVE=true` skips the final self-terminate; the watchdog still
terminates after `MAX_RUNTIME_S`: **2 hours** in test mode. Production deadline: derived
from the total audio duration with a margin.

**A7. R2 credentials in the pod.** Default: bucket-scoped R2 token as Runpod Secrets, referenced
from a Runpod template managed by the desktop app.

**A8. Python.** Default: 3.13 for every member.

**A9. Desktop stack.** Default: PySide6, PyInstaller one-file, static LGPL ffmpeg bundled, four
unsigned builds.

**A10. Local state.** SQLite (WAL) through SQLAlchemy 2.0 ORM, schema managed by Alembic.
Migrations are bundled into the executable and applied at start-up.

**A11. Repository layout.** Default: uv workspace with `packages/contracts`, `apps/desktop`,
`apps/worker`.

**A13. CI scope.** Default, plus: every desktop release from `main` also creates a GitHub
Release with the four executables attached.

**A14. Lint and code layout.** Lint adjustments as proposed. Code layout follows a Django-like
structure: every app and sub-app (package) keeps its constants in its own `constants.py`, each
with a short one-line comment on its purpose (no module-level `_CONSTANT`s at the top of other
files); SQLAlchemy models in `models.py`; Pydantic models in `classes.py`; Alembic migrations in
a separate `migrations/` folder.

**A15. Real-infrastructure checks.** Runpod checks run only while the architect is present and
after an explicit go, one at a time, never started autonomously. Integration tests against real
R2/Runpod are marked and excluded from the default and CI runs.

**A16. Review gate hook.** Default: keep as implemented.

**A17. External reaper.** Default: scheduled GitHub Actions workflow with a pods-only key.
