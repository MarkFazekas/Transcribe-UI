"""Batch ids: `batch_YYYYMMDD_HHmmss` from the local creation time, `_2`, `_3`, ... on collision (§3.3)."""

from datetime import datetime, tzinfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from transcribe_ui.batches.constants import BATCH_ID_FORMAT, BATCH_ID_SUFFIX_SEPARATOR, FIRST_BATCH_ID_SUFFIX
from transcribe_ui.state.models import BatchRow


def base_batch_id(moment: datetime, zone: tzinfo | None) -> str:
    """Return the batch id of a creation time, in the given time zone (None: this computer's)."""
    local = moment.astimezone(zone)
    return local.strftime(BATCH_ID_FORMAT)


def free_batch_id(session: Session, base: str) -> str:
    """Return `base`, or the first free `base_<n>` (n from 2) when it is taken.

    Call it in the transaction that inserts the batch: transactions start with `BEGIN
    IMMEDIATE`, so two requests creating a batch in the same second cannot both pick one id.
    """
    prefix = f"{base}{BATCH_ID_SUFFIX_SEPARATOR}"
    candidates = select(BatchRow.batch_id).where(
        (BatchRow.batch_id == base) | BatchRow.batch_id.startswith(prefix, autoescape=True)
    )
    taken = frozenset(session.scalars(candidates))
    if base not in taken:
        return base
    suffix = FIRST_BATCH_ID_SUFFIX
    while f"{prefix}{suffix}" in taken:
        suffix += 1
    return f"{prefix}{suffix}"
