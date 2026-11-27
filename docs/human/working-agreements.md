# Working agreements (human input)

Source: the architect's initial brief (2026-11-26) and later chat input (2026-11-27),
translated to English.

## Roles and communication

- The repo owner acts as **architect**, not product. Product questions (UI, behaviour) go to
  product via `docs/ai/questions-for-product.md`. Architecture, hosting, CI and implementation
  decisions are agreed with the architect via `docs/ai/questions-for-architect.md` and chat.
- Chat may be in Hungarian. The project itself (code, docs, commits, PR texts, product
  questions) is in English.
- Interim reports are short. If compacting would save tokens without hurting quality, the last
  line of a report is `Compactable: True`; otherwise the line is omitted. No explanation either
  way.

## Autonomy and blockers

- Work continues until a real blocker. Do not stop for a question that can be written down.
- Open product questions go to `docs/ai/questions-for-product.md` with numbered IDs (Q1, Q2,
  ...), each with a proposed default, so answering "default" is enough. Surface them early,
  especially before a product meeting.
- Developer/architect questions go to `docs/ai/questions-for-architect.md` (IDs A1, A2, ...)
  and are raised in chat.
- Unimportant questions must not become blockers: decide sensibly, record the decision, move
  on. If confirmation still seems necessary, state the decision situation, the decision taken
  and why.
- When the answer is "default", apply the proposed default and record it.
- Answered questions are taken out of the question files; the answers and what follows from
  them are put in their place in the docs.

## Session continuity

- The repo tracks the state of the work, so the architect does not have to copy handoff notes
  between sessions: telling a new session to continue is enough for it to pick up the work.

## Quality first

- Never compromise quality or usability because of usage quota. Stopping and resuming later is
  fine; nothing is lost.
- If a stop is imminent (quota or `/exit`), stop in an orderly way: finish the step in progress
  or save its state, and do not start anything new.

## Sub-agents and models

- Run as many sub-agents as give a real speed-up without hurting quality. Do not parallelise
  when coordination, conflicts or merges cost more than parallelism gains.
- Use Sonnet (or Haiku where adequate) where it does not hurt quality; sub-agents may start
  cheaper sub-agents under the same condition.
- The main session can run at medium effort; code reviews and concurrency or
  failure-handling work stay at high effort.
- The code review is started by the main session, not by the sub-agent that wrote the code.
- Side questions (research, "would X help?") are handled by sub-agents so the main thread does
  not stall.

## Git and CI

- Conventional commits. No Claude co-author or other attribution lines in commits, and no
  "Generated with Claude Code" or similar line in PR descriptions.
- Local rebase is fine. Never force push.
- Never work directly on `main`: branch + PR; a sub-agent reviews each PR before merge.
- `main` is protected: PR required, signed commits required, status checks must pass.
- A few minutes after a merge, re-check the CI runs. Waiting for CI must not block other work,
  but the result must be checked.
- CI is scoped sensibly: release builds only when relevant code (UI app, deployed app) changed;
  no release on PRs. The only uploaded artifact is the runnable single-file executable, with no
  extra zip or other files.
- Local lint, typecheck and tests run in parallel (UI app and deployed app at the same time),
  but no parallelism without real gain (e.g. no pytest-xdist).
- Finished fixes are not thrown away even if the experiment they belonged to is dropped.

## Secrets and data

- Passwords or credentials never get committed. Remove them from received data files before
  committing.
- During development, credentials live in an uncommitted local `.env` file; in CI in GitHub
  repository or environment secrets.

## Real infrastructure

- Tests or checks that start anything on Runpod run only while the architect is present and
  after an explicit go, one at a time. Nothing is started on Runpod autonomously.
- The read-only Runpod token may be used without the architect.

## Process gate

- First: create the repo. Then research (Runpod deploy, templates, the referenced repos, docs)
  and surface open questions for both architect and product.
- Before development starts, the architect reviews the technical docs. After that,
  development proceeds until the next blocker.
