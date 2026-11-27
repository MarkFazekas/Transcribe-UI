# Status

Current state of the work, so a new session can continue from "continue" alone. Only the
present state and the plan: no history (git has it), no design and no milestone list (both
live in `architecture.md`; milestones in §10). Update this file in every PR that changes the
state (an item done, a new blocker, a changed plan). Before an orderly stop, commit and push
the status change on the current branch. On a merge conflict in this file, re-derive it from
`gh pr list` and architecture §10 rather than merging line by line.

## Resuming a session

1. `git fetch --prune`, then reconcile this file with reality. This copy on `origin/main` lags
   behind open branches; for each open branch or PR, `git show origin/<branch>:docs/ai/status.md`
   overrides it.
   - `git worktree list`, and `git status` in every worktree: uncommitted work, stale worktrees.
   - `git branch -a`, `gh pr list`: unfinished branches, open PRs with review / CI state.
   - `gh run list -L 10 --branch main --event push`: CI and release runs on `main`, failures
     first; `gh run list -L 3 --workflow reaper.yml` for the scheduled reaper.
2. Unmerged work on a branch: inspect it against `origin/main` (`git log origin/main..<branch>`,
   `git diff origin/main...<branch>`) and continue it or start over; record which.
3. Then take the first item of "Next" that is not blocked; when "Next" is empty, plan the
   next milestone of architecture §10.

## In progress

- M3 reaper on branch `feat/reaper` (design: architecture §5.2, §8).

## Waiting on humans

- Architect: create the pods-only Runpod key and store it as `RUNPOD_REAPER_API_KEY` in a
  `runpod-reaper` GitHub environment restricted to `main`. Until then the reaper is inactive.
- Architect: schedule the M4 approved Runpod run.
- Product: Q22 (Chinese and Japanese line length) in `questions-for-product.md`.

## Next

1. Merge the reaper; check its first scheduled run.
2. Check the release-worker run on `main` pushed the image tags `sha-<short>`, `latest` and
   `schema-1` to `markfazekasgh/transcribe-ui-worker`.
3. M5 desktop backend (M4 runs whenever the architect is available).
