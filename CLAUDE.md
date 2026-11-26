# CLAUDE.md

Transcribe-UI: a desktop app (GUI + CLI, later MCP) that selects local audio/video files,
uploads their audio to Cloudflare R2 in resumable batches and starts a Runpod GPU worker that
transcribes them with Whisper into subtitles; the app then places the subtitles locally.

## Read first

- `docs/human/requirements.md` — what was asked (source of truth for scope).
- `docs/human/working-agreements.md` — roles, communication, git/CI and secret rules.
- `docs/ai/architecture.md` — current design. `docs/ai/tooling.md` — lint, tests, CI, hooks.
- Open questions: `docs/ai/questions-for-product.md` (Q#), `docs/ai/questions-for-architect.md` (A#).

## Working rules (summary of docs/human/working-agreements.md)

- The user is the **architect**. Architecture, hosting, CI and implementation questions go to
  `questions-for-architect.md` and chat; product (UI/behaviour) questions go to
  `questions-for-product.md`, each with a proposed default. "default" means: apply the default
  and record it under "Decided".
- Chat may be Hungarian; everything in the repo is English.
- Keep interim reports short. If compacting would not hurt quality, end the report with the
  line `Compactable: True`; otherwise omit the line.
- Work until a real blocker; record small decisions instead of asking. Never trade quality for
  quota; stop in an orderly way (finish or save the current step, start nothing new).
- Use sub-agents only where they give real speed-up; Sonnet/Haiku where quality allows.

## Git

- Never commit on `main`. Branch → PR → `code-reviewer` sub-agent review → merge.
- Conventional Commits; no co-author or attribution trailers. Signed commits (configured).
- Local rebase is fine; **never force push**.
- After a merge, re-check the CI runs a few minutes later without blocking other work.
- Never commit credentials. Local secrets live in `.env` (git-ignored); CI uses GitHub secrets.

## Code

- uv workspace, Python 3.13: `packages/contracts` (shared Pydantic models),
  `apps/desktop` (backend + frontends), `apps/worker` (Runpod worker).
- Business logic lives only in the backend. Frontends (GUI, CLI, future MCP) build Pydantic
  requests and render responses; the same request must produce the same response from every
  frontend. Clock, ID generation and external clients are injected so this stays testable.
- Strict quality bar: ruff (`ALL`) + flake8 with wemake and other plugins + mypy strict. Fix
  findings rather than suppressing them; any `noqa` names the code and gives a reason.
- Tests: few unit tests; mostly regression and flow tests (see architecture §7).
- Run checks from the repo root: `uv run ruff format .`, `uv run ruff check .`,
  `uv run flake8 .`, `uv run mypy <src>`, `uv run pytest <tests>` (or `scripts/check.py` once
  it exists, which runs members in parallel).

## Documentation

- `docs/human/`: only what humans (architect, product) said. Record new human input here,
  translated to English, without interpretation.
- `docs/ai/`: what Claude derives from inputs and discoveries: architecture, tooling,
  questions, and `research/` for reusable external facts (with sources).
- Keep information that stays useful; no change history or narratives (git has the history).
  Update the relevant doc in the same PR as the code that changes the behaviour.
- When a question is answered, move it to "Decided" in its file and update the affected docs.

## Hooks

A Stop hook (`.claude/hooks/review_gate.py`) asks for a `code-reviewer` sub-agent review when
files changed during the turn. Address high/medium findings (or justify) before finishing.
