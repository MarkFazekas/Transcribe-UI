"""Recognising this database's own manifest of a batch in R2 (architecture §3.3 "Withdrawing a leftover manifest").

An object at `batch_metadata/<id>.json` is ours when its bytes have the digest a pending
withdrawal recorded, or when it reads as a manifest with the batch's own `created_at` and
`scan_root` (the batch as this database built it, from any state). Another computer's batch with the same id has
another creation time, so it is never taken for ours.

The same recognition decides which batches in R2 count as this database's (`known`,
architecture §3.9 "Known batches"): the launch scope, `list_cloud_batches` and the cleanup of
unknown batches use `is_known`.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from transcribe_contracts.errors import ContractError
from transcribe_contracts.layout import manifest_key
from transcribe_contracts.reading import read_manifest
from transcribe_ui.batches.payloads import BatchPayload, PendingWithdrawal
from transcribe_ui.batches.phases import load_batch
from transcribe_ui.state.models import BatchRow
from transcribe_ui.upload.manifest import withdrawing_batch_ids


def manifest_digest(body: bytes) -> str:
    """Return the SHA-256 (hex) a manifest is recognised by."""
    return hashlib.sha256(body).hexdigest()


def pending_digest(stored: object) -> str | None:
    """Return the digest a stored `pending_withdrawal` recorded (None when nothing is pending)."""
    return None if stored is None else PendingWithdrawal.model_validate(stored).manifest_sha256


@dataclass(frozen=True)
class OwnManifest:
    """How this database recognises its own manifest of one batch."""

    batch_id: str
    digests: frozenset[str]
    created_at: datetime
    scan_root: str

    def owns(self, body: bytes) -> bool:
        """Return whether the object's bytes are this database's manifest of the batch."""
        if manifest_digest(body) in self.digests:
            return True
        try:
            manifest = read_manifest(manifest_key(self.batch_id), body)
        except ContractError:
            return False
        return (manifest.created_at, manifest.scan_root) == (self.created_at, self.scan_root)


def is_known(session: Session, batch_id: str, body: bytes) -> bool:
    """Return whether the manifest in R2 is this database's batch: its id is here and it was adopted, or the manifest is recognised as ours."""
    row = session.get(BatchRow, batch_id)
    if row is None:
        return False
    if BatchPayload.model_validate(row.payload).adopted is not None:
        return True
    return own_manifest(session, batch_id).owns(body)


def own_manifest(session: Session, batch_id: str) -> OwnManifest:
    """Return how to recognise the batch's own manifest, from the database as it is now."""
    row = load_batch(session, batch_id)
    pending = pending_digest(row.pending_withdrawal)
    payload = BatchPayload.model_validate(row.payload)
    known = frozenset(() if pending is None else (pending,))
    return OwnManifest(batch_id, known, row.created_at, payload.scan_root)


def withdrawing_digests(session: Session) -> dict[str, str]:
    """Return batch id -> digest of the leftover manifest each pending withdrawal will delete."""
    stored = {batch_id: load_batch(session, batch_id).pending_withdrawal for batch_id in withdrawing_batch_ids(session)}
    digests = {batch_id: pending_digest(pending) for batch_id, pending in stored.items()}
    return {batch_id: digest for batch_id, digest in digests.items() if digest is not None}
