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

- None.

## Waiting on humans

- Architect: create the pods-only Runpod key and store it as `RUNPOD_REAPER_API_KEY` in a
  `runpod-reaper` GitHub environment restricted to `main`. Until then the reaper workflow only
  warns "reaper disabled" and does nothing.
- Architect: schedule the M4 approved Runpod run.
- Product: Q22 (Chinese and Japanese line length), Q23 (placement when the original folder is
  gone), Q24 (download before the job is done), Q25 (download again), Q26 (abandoning a job
  before upload), Q27 (upload finishing during a run), Q28 (unknown jobs in the cloud), Q29
  (duplicate backup volumes) in `questions-for-product.md`. M5 is planned on the proposed defaults.
- Architect: A18 (R2 credentials for the pod), A19 (maximum wait for a machine) in
  `questions-for-architect.md`.

## Next

1. Check the first scheduled reaper runs on `main` (expected: the "reaper disabled" warning).
2. M5 wave 4 per `docs/ai/plans/m5.md` §4 (M5.1-M5.11 are built): M5.12 (run monitoring)
   next, then M5.13 and M5.14; M4 runs whenever the architect is available.
3. M5.14 builds `create_retry_batch` on the M5.10 copy variant: retry items carry
   `ItemPayload.copy_sources` (`upload/payloads.py` `CopySource`) and are created in phase
   `reviewed` in an `uploading` batch; `run_upload` (or the retry request through the same
   path) copies, verifies and publishes them. Cloud cleanup of an `uploading` batch also
   empties its temp folder (`upload/temp.py` `clear_temp`). Retry batches are created in
   `uploading`, so `run_upload` never runs the `batch_id_taken` check for them:
   `create_retry_batch` must run the same cross-computer check (`upload/start.py`: manifest,
   objects and open multipart uploads under the id) before it creates the batch.
   `CopySource.source_key` must be the layout's audio key of its track and extension.
4. M5.12 follows runs through `runs/records.py` (`RunPayload` in `runs/payloads.py`; it adds the
   first `RUNNING` sighting), resolves `launching` runs with `runs/settling.py`
   `resolve_launching`, terminates with `runs/stopping.py` `terminate_verified` (a `KnownPod`:
   a pod id known only from a queued pod is not seen, so a 404 counts as gone only after
   `DEPLOY_SETTLE_S`; the run payload keeps `pod_seen` and `pod_deployed_at`), and calls
   `publish_held_manifests` under the runs lock once no run is active.
