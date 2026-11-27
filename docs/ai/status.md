# Status

Current state of the work, so a new session can continue from "continue" alone. Only the
present state and the plan: no history (git has it), no design and no milestone list (both
live in `architecture.md`; milestones in §10). Update this file in every PR that changes the
state (an item done, a new blocker, a changed plan). Before an orderly stop, commit and push
the status change on the current branch. On a merge conflict in this file, re-derive it from
`gh pr list` and architecture §10 rather than merging line by line.

## Resuming a session

1. `git fetch --prune`, then reconcile this file with reality. This copy on `origin/main` lags
   behind open branches: where a branch changed this file
   (`git diff origin/main...origin/<branch> -- docs/ai/status.md`), apply those changes section
   by section; a branch that did not change it says nothing about the state.
   - `git worktree list`, and `git status` in every worktree: uncommitted work, stale worktrees.
   - `git branch -a`, `gh pr list`: unfinished branches, open PRs with review / CI state.
   - `gh run list -L 10 --branch main --event push`: CI and release runs on `main`, failures
     first; `gh run list -L 3 --workflow reaper.yml` for the scheduled reaper (once it is on
     `main`).
2. Unmerged work on a branch: inspect it against `origin/main` (`git log origin/main..<branch>`,
   `git diff origin/main...<branch>`) and continue it or start over; record which.
3. Then take the first item of "Next" that is not blocked. A milestone without its
   `docs/ai/plans/mN.md` (architecture §10) is planned before its first PR; when "Next" is
   empty, plan the next milestone.

## In progress

- M5.14 (retry, cloud cleanup and the end-to-end flow; marks M5 **(built)**) on
  `feat/m5.14-retry-cleanup`, ready for its PR.

## Waiting on humans

- Architect: schedule the M4 approved Runpod run.
- Product: Q22 (Chinese and Japanese line length), Q23 (placement when the original folder is
  gone), Q24 (download before the job is done), Q25 (download again), Q26 (abandoning a job
  before upload), Q27 (upload finishing during a run), Q28 (unknown jobs in the cloud), Q29
  (duplicate backup volumes) in `questions-for-product.md`. M5 is built on the proposed defaults;
  an answer that differs changes the part that implements it.

## Next

1. The reaper is live: `RUNPOD_REAPER_API_KEY` (scope: Pods read+write only) is in the
   `runpod-reaper` environment (deployment branch policy: `main` only); its first real run on
   `main` (run 45975242900, 2026-11-27) passed ("0 queued pods listed, 0 pods listed, pass
   ok"). `gh run list -L 3 --workflow reaper.yml` shows whether it keeps passing.
2. M5 is built. Next milestone: M6 (desktop frontends, architecture §10), which needs its plan
   `docs/ai/plans/m6.md` first. M4 runs whenever the architect is available.
3. From now on every database schema change, including a change of a stored payload's shape,
   is a new Alembic revision (architecture §3.8); the initial revision is no longer edited.
4. Desktop follow-ups for M6: the GUI shows `BatchSummary.cleanup_pending` and offers to repeat
   the cleanup (launches are refused with `cleanup_pending` until then), and runs
   `reconcile_cloud` at start-up and `refresh_status` while a run is active (plan m5 §1).
