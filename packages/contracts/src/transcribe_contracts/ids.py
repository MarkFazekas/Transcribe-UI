"""ULID generation (item ids and run ids; architecture §4)."""

import secrets
from datetime import datetime

from transcribe_contracts.constants import ULID_ALPHABET, ULID_LENGTH, ULID_RANDOM_BITS, ULID_TIME_BITS


def new_ulid(moment: datetime, randomness: int | None = None) -> str:
    """Return a canonical upper-case ULID for an aware moment.

    Args:
        moment: Time encoded in the first 48 bits (epoch_mseconds since the Unix epoch).
        randomness: The 80 random bits; drawn from `secrets` when None (tests pass a value).

    Raises:
        ValueError: If the moment is naive, before the epoch, or the randomness is out of range.
    """
    if moment.tzinfo is None:
        msg = "moment must be timezone-aware"
        raise ValueError(msg)
    epoch_ms = int(moment.timestamp() * 1000)
    if not 0 <= epoch_ms < 1 << ULID_TIME_BITS:
        msg = f"moment {moment.isoformat()} cannot be encoded in a ULID"
        raise ValueError(msg)
    random_part = secrets.randbits(ULID_RANDOM_BITS) if randomness is None else randomness
    if not 0 <= random_part < 1 << ULID_RANDOM_BITS:
        msg = "randomness must fit in 80 bits"
        raise ValueError(msg)
    number = (epoch_ms << ULID_RANDOM_BITS) | random_part
    chars = []
    for _ in range(ULID_LENGTH):
        number, digit = divmod(number, len(ULID_ALPHABET))
        chars.append(ULID_ALPHABET[digit])
    return "".join(reversed(chars))
