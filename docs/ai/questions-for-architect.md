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
`publish_held_manifests`) is removed, and "uploaded, waits for the current run" is shown when a
live lease does not list the batch. Withdrawing a leftover manifest (§3.3) is refused only while
a live lease lists the batch.
Every installation reads activity from R2 (a lease before its deadline or a fresh heartbeat),
and an installation with the Runpod key also from the Runpod lists, as now. Upload,
`refresh_status` (its R2 steps) and cleanup open the Runpod client only when a key is set.
Changes: contracts (the lease model and key), worker discovery (§5.3), desktop launch, upload
and cleanup; schema minor version (a new object; the manifest is unchanged).

### A22. No absolute local paths in the shared manifest

The manifest stores `scan_root` and every item's `original_path` as absolute paths of the
uploading computer (§4). Since Q28, other people's installations read manifests (the start list,
the download of another computer's job, §3.9), so user names and folder layouts of the uploader's
computer are visible to everyone with bucket access. The worker uses neither field; the desktop
uses `scan_root` only to recognise its own leftover manifest (`upload/leftover.py`).

Proposed default: manifest schema 2.0 replaces both with `root_name` (the last folder name of
the scan root, for display), each item's `relative_path` (the `/`-separated folder names and
the file name below the scan root; the validator refuses empty, `.`, `..` and absolute parts)
`origin` (a random installation id, a ULID created with the local database) and
`origin_name` (the installation's name, Q35). The absolute
paths stay in the local database only. Leftover recognition compares `created_at` and
`origin`. Contracts, the worker (it refuses 1.x manifests, per the versioning rule) and the
desktop manifest builder change together; nothing is deployed yet, so no 1.x manifest needs
to be read.
