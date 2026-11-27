# Questions for the architect

Answer each with "default" to accept the proposed default, or with a different decision.
Answered questions are removed from this file; the answer goes to
`docs/human/requirements.md` and its consequences to the affected docs. IDs are never reused
(next: A23).
Background for most items: `docs/ai/architecture.md` and `docs/ai/research/`.

## Open

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
