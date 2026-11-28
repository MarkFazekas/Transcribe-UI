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

- M6.13 (several installations on one bucket, with its GUI) on its own branch: in progress;
  M6's only open part.
- M8.4 (the macOS GUI as `Transcribe-UI.app` in a `.dmg`, A23 answered "default") in PR #57
  on `feat/m8.4-macos-app`, with `main` (M8.2, #56) merged in: the PR's `release-desktop.yml`
  runs proved the app, its `Info.plist`, the floor check inside it, `hdiutil`, strict signing
  and `gui-signature` on both macOS legs; with M8.2 merged, the next run also proves ffmpeg and
  ffprobe inside the app.

## Waiting on humans

- Questions: A24 (architect) and Q36 (product), both for M8; none blocks M6. M5 is built
  and M6 is planned.

## Next

1. The reaper is live: `RUNPOD_REAPER_API_KEY` (scope: Pods read+write only) is in the
   `runpod-reaper` environment (deployment branch policy: `main` only); its first real run on
   `main` (run 45975242900, 2026-11-27) passed ("0 queued pods listed, 0 pods listed, pass
   ok"). `gh run list -L 3 --workflow reaper.yml` shows whether it keeps passing.
2. M6 (desktop frontends) is planned in `docs/ai/plans/m6.md`. Merged: M6.1, M6.2, M6.14
   (#40), M6.12 (#41), M6.7 (#42), M6.5, M6.3 (#45), M6.4 (#46), M6.6 (#47), M6.9 (#48,
   ebae048), M6.10 (a022e03), M6.8 (#50, 6ad5b0f), M6.11 (#52, db1810c). Open: M6.13
   (several installations on one bucket, Q28, with its GUI), M6's only open part; its merge
   marks M6 **(built)** in architecture §10. The M4
   Runpod trial run is deferred until everything else works (architect); nothing waits on it,
   and it still needs the architect present and their go.
3. M8 (desktop release) is planned in `docs/ai/plans/m8.md` (merged, #51); M8.1 is merged
   (#53, c43bf73), M8.2 is merged (#56, c63861e), M8.4 is in PR #57, and M8 does not wait
   for M7. M8.3 and M8.5
   take the FFmpeg and zlib sources, licences and configure lines from
   `scripts/build_ffmpeg_pins.py` (`build_info`) or `ffmpeg-build.json` of the build. M8.6
   (README for users, screenshots script, `docs/cli.md`) follows M8.5 (which marks M8 built),
   and M7 if M7 is done by then, and comes before MCP (architect).
4. From now on every database schema change, including any change of a stored payload's
   shape (an added field too), is a new Alembic revision (architecture §3.8); the initial
   revision is no longer edited.
5. M5 follow-ups: the repeat of an interrupted cleanup is built by M6.10 (M6.5 shows
   `cleanup_pending`, M6.6 links its launch refusal to the job). `reconcile_cloud` at start-up
   and `refresh_status` while a run is active are built in M6.6.
