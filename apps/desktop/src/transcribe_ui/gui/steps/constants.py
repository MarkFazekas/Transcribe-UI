"""Constants of the step header (plan m6 §1 "Layout")."""

from typing import Final, Literal

# Where a step stands for a job: before the job's stage, the job's stage, or after it.
type StepState = Literal["done", "current", "upcoming"]

# The steps of the guided flow in order, by their `BatchStage` code (the stage `done` is past all of them).
STEPS: Final = ("files", "review_upload", "transcription", "download")
# Shown before the title of a step that is done.
DONE_MARK: Final = "✓"
# Shown between two steps.
STEP_SEPARATOR: Final = "→"
