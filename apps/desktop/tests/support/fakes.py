"""Deterministic port fakes."""

import random
from dataclasses import dataclass
from datetime import datetime

from transcribe_ui.api.ports import Ports, UlidSource

# The fixed "now" of every test backend.
FIXED_NOW = datetime.fromisoformat("2026-11-27T12:00:00+00:00")
# Seed of the id source; both parity legs use it, so they create the same ids.
ID_SEED = 20261127


@dataclass(frozen=True)
class FixedClock:
    """A clock that never moves."""

    moment: datetime = FIXED_NOW

    def now(self) -> datetime:
        """Return the fixed moment."""
        return self.moment


def seeded_ports(seed: int = ID_SEED) -> Ports:
    """Return ports with a fixed clock and seeded ULID randomness."""
    clock = FixedClock()
    generator = random.Random(seed)  # noqa: S311 -- reproducible test ids, not cryptography.
    return Ports(clock=clock, ids=UlidSource(clock, generator.getrandbits))
