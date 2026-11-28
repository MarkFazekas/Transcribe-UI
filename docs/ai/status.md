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

- M6.14 (language of the texts) on `feat/m6.14-i18n`: built and checked locally; waiting for
  its PR and review.
- M6.12 (remaining-time estimate, backend) on `feat/m6.12-remaining-time`: built and checked
  locally; waiting for its PR and review.

## Waiting on humans

- Questions: none open (product and architect). M5 is built and M6 is planned.

## Next

1. The reaper is live: `RUNPOD_REAPER_API_KEY` (scope: Pods read+write only) is in the
   `runpod-reaper` environment (deployment branch policy: `main` only); its first real run on
   `main` (run 45975242900, 2026-11-27) passed ("0 queued pods listed, 0 pods listed, pass
   ok"). `gh run list -L 3 --workflow reaper.yml` shows whether it keeps passing.
2. M6 (desktop frontends) is planned in `docs/ai/plans/m6.md`. Wave 1: M6.1 (backend
   additions) and M6.2 (GUI foundation) are merged.
   Then wave 2: M6.14 (texts in English and Hungarian, Q32; built on its branch) first, then M6.5 (it changes the
   shared goldens once) before M6.4 and M6.6 start, then M6.4, M6.6 and M6.12 (the
   remaining-time estimate, backend), followed by M6.3 and M6.7; wave 3 M6.8, M6.9, M6.10; wave
   4 M6.11. M6.13 (several installations on one bucket, Q28, with its GUI) after M6.6 and M6.9;
   the later of M6.11 and M6.13 marks M6 built. The M4 Runpod trial run is deferred until
   everything else works (architect); nothing waits on it, and it still needs the architect
   present and their go.
3. From now on every database schema change, including any change of a stored payload's
   shape (an added field too), is a new Alembic revision (architecture §3.8); the initial
   revision is no longer edited.
4. M5 follow-ups, planned in M6: `cleanup_pending` shown (M6.5) and the cleanup repeated
   (M6.10); `reconcile_cloud` at start-up and `refresh_status` while a run is active (M6.6).
