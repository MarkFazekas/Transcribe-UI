"""Pydantic payload of the `placements` rows (the JSON column of `state/models.py`)."""

from transcribe_ui.api.base import FrozenModel


class PlacementPayload(FrozenModel):
    """What a placement row keeps besides its job, batch, outcome and time."""

    # Where the subtitle went (or would have gone); None when the job had no target.
    target_path: str | None
