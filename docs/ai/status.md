# Status

Current state of the work, so a new session can continue from "continue" alone. Only the
present state and the plan: no history (git has it). Update this file in every PR that
changes the state (an item done, a new blocker, a changed plan), and before an orderly stop.

## Resuming a session

1. Read this file, then reconcile it with reality (this file on `main` lags behind open
   branches):
   - `git status`, `git branch -a`, `git worktree list`: unfinished local work, stale
     worktrees or branches.
   - `gh pr list`: open PRs and their review / CI state.
   - `gh run list -L 10`: recent CI and release runs; failed ones on `main` come first.
2. Unmerged work on a branch: inspect it (`git log main..<branch>`, `git diff main...<branch>`)
   and continue it or start over; record which.
3. Then take the first item of "Next" that is not blocked.

## Milestones

| # | Milestone | Scope (architecture) | State |
|---|---|---|---|
| M1 | Foundation | repo, uv workspace, lint/type/test tooling, CI, `contracts` (§2, §4, §8) | done |
| M2 | Worker | pipeline, subtitles, entry/watchdog self-termination, image + smoke test + Docker Hub release (§5) | done; first `main` release run to check |
| M3 | Cloud safety net | pod name convention, external reaper (§5.2 layer 4) | in progress |
| M4 | Desktop backend | façade, discovery/selection, batches, conversion + resumable R2 upload, credentials, worker launch and monitoring, download and placement (§3.1-3.7) | not started |
| M5 | Desktop frontends | GUI and CLI over the same requests (§3.1) | not started |
| M6 | First real end-to-end run | approved Runpod run with the architect: open points of §7 and `research/runpod.md` | not started; needs the architect |
| M7 | Desktop release | `release-desktop.yml` one-file builds for the four platforms (§8) | not started |
| later | MCP frontend | third frontend over the same requests | not planned yet |

## In progress

- **Pod reaper** (branch `feat/reaper`, architecture §5.2 layer 4, §8): pod name convention
  `transcribe-ui-<run_id>-until-<YYYYMMDDTHHMMZ>` in `packages/contracts`, the
  `transcribe_worker.reaper` package and `.github/workflows/reaper.yml` (every 15 minutes,
  `runpod-reaper` environment, secret `RUNPOD_REAPER_API_KEY`).

## Waiting on humans

- Architect: create the pods-only Runpod key and store it as `RUNPOD_REAPER_API_KEY` in a
  `runpod-reaper` GitHub environment restricted to `main`. Until then the reaper workflow
  fails on schedule.
- Product: Q22 (Chinese and Japanese line length) in `questions-for-product.md`.

## Next

1. Merge the reaper; check its first scheduled run (fails until the key exists).
2. Check the release-worker run on `main` pushed the image tags `sha-<short>`, `latest` and
   `schema-1` to `markfazekasgh/transcribe-ui-worker`.
3. M4 desktop backend, then M5.

## Needs the architect present

- First real Runpod run (architecture §7): verify that the pod-scoped key can delete its own
  pod, Runpod secret resolution in a direct-create `env`, and the other open points in
  `research/runpod.md` ("Items to test with a real key").
