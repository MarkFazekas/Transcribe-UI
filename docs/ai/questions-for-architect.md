# Questions for the architect

Answer each with "default" to accept the proposed default, or with a different decision.
Answered questions are removed from this file; the answer goes to
`docs/human/requirements.md` and its consequences to the affected docs. IDs are never reused
(next: A23).
Background for most items: `docs/ai/architecture.md` and `docs/ai/research/`.

## Open

### A21. Keeping a run's scope fixed when another computer uploads or starts runs

Q28's answer allows an installation with R2 credentials only (no Runpod key) to upload jobs
that another installation starts (architecture §3.9). Today Q27's guarantee ("a batch uploaded
after Start waits for the next run") rests on the manifest step's activity check, which needs
the Runpod key (fail closed) and only covers this computer's view: between one computer's check
and its manifest upload, another computer can start a run, and the worker then takes a batch its
deadline and estimate did not include. Upload, cleanup and `refresh_status` also open the
Runpod client today, so an R2-only installation cannot work at all.

Options: (1) keep the held manifests and give R2-only installations an R2-only activity check
(heartbeats plus a launch lease object); the cross-computer race stays. (2) The launching
desktop writes `runs/<run_id>/launch.json` (desktop-written; the covered batch ids, the pod
name deadline) before the pod create, and the worker processes only the batches listed there
(no file: the worker processes nothing and reports why). The scope is then fixed by the run
itself, whatever any computer publishes later.

Proposed default: (2). Manifests are then published as soon as an upload is verified, on any
installation, without an activity check; the held-manifest machinery (`manifest_held`,
`publish_held_manifests`) is removed, and the batch label `waits_for_current_run` (added by
M6.8 from `manifest_held`) is computed from live leases: set while a live lease does not list
the batch. A lease is live until its deadline, and stops counting
once the run's `status.json` is final. Cleanup and the withdrawal of a leftover manifest (§3.3)
are refused only for batches a live lease lists (or, with the Runpod key, while a run without a
lease is seen, as now). The worker skips a listed batch whose manifest has disappeared (a
cleanup between the lease and its start) instead of failing. Every installation reads activity
from R2 (live leases and fresh heartbeats), and an installation with the Runpod key also from
the Runpod lists. Upload,
`refresh_status` (its R2 steps) and cleanup open the Runpod client only when a key is set.
Changes: contracts (the lease model and key), worker discovery (§5.3), desktop launch, upload
and cleanup; schema minor version (a new object; the manifest is unchanged).

### A22. Absolute local paths in the shared manifest

The manifest stores `scan_root` and every item's `original_path` as absolute paths of the
uploading computer (§4), as the requirements ask ("UI app: conversion and upload": "Each file's
original absolute path is stored in the batch metadata"). Since Q28, other people's
installations read manifests (the start list, the download of another computer's job, §3.9), so
user names and folder layouts of the uploader's computer are visible to everyone with bucket
access. The worker uses neither field; the desktop uses `scan_root` to recognise its own
leftover manifest (`upload/leftover.py`), and another computer derives the relative folders and
file names from `scan_root` and `original_path`.

Proposed default: keep the manifest as it is (absolute paths, as the requirement says); other
computers derive the relative paths locally and refuse unsafe parts (§3.9). No contract change.

Alternative: manifest schema 2.0 with only the scan root's last folder name and each item's
path relative to it; the absolute paths stay in the uploader's database. This changes the
requirement above, the contracts, the worker (it refuses 1.x manifests) and leftover
recognition, which could then compare the manifest's `uploaded_by` installation id (Q35,
architecture §3.9) instead of `scan_root`.
