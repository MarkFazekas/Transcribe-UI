"""One job: transcribe, check quality, re-run carefully if needed, build the subtitle."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from transcribe_contracts.classes import EngineInfo, JobResult, JobStatus
from transcribe_worker.engine.classes import Transcript
from transcribe_worker.engine.host import EngineError
from transcribe_worker.pipeline.constants import CAREFUL_TIMEOUT_FACTOR, JOB_TIMEOUT_PER_AUDIO_S
from transcribe_worker.pipeline.plan import PlannedJob
from transcribe_worker.subtitles import srt as srt_format
from transcribe_worker.subtitles.cues import build_cues
from transcribe_worker.subtitles.quality import clean, find_problems

logger = logging.getLogger(__name__)


class Engine(Protocol):
    """The engine as the pipeline uses it (see `engine.host.EngineProcess`)."""

    def settings(self, *, careful: bool) -> EngineInfo:
        """Return the settings of a mode."""

    def transcribe(self, audio: Path, language: str, *, careful: bool, timeout_s: float) -> Transcript:
        """Transcribe one decoded file.

        Raises:
            EngineError: The job failed, the engine crashed, or the timeout passed.
        """


@dataclass(frozen=True)
class JobContext:
    """What every job of a run shares."""

    run_id: str
    engine: Engine
    now: Callable[[], datetime]
    min_timeout_s: float


@dataclass(frozen=True)
class JobOutput:
    """A finished job: its result (the "done" marker) and its subtitle (empty unless ok)."""

    job_result: JobResult
    srt: bytes


@dataclass(frozen=True)
class _Attempt:
    transcript: Transcript
    careful: bool


def transcribe_job(context: JobContext, planned: PlannedJob, wav: Path | None, problem: str | None) -> JobOutput:
    """Process one job; any failure becomes a "failed" result instead of an exception.

    Args:
        context: Run id, engine and clock.
        planned: The job.
        wav: Decoded audio, or None if preparing it failed.
        problem: Why preparing the audio failed (set exactly when `wav` is None).
    """
    started_at = context.now()
    careful = planned.job.mode == "careful"
    try:
        attempt = _transcribe_checked(context, planned, wav, problem)
    except EngineError as error:
        logger.warning("job %s failed: %s", planned.json_key, error)
        failure = _Failure(careful=careful, error=str(error))
        return _output(context, planned, started_at, failure)
    cues = build_cues(clean(attempt.transcript))
    srt = srt_format.to_srt(cues).encode()
    return _output(context, planned, started_at, _Success(attempt, len(cues)), srt)


@dataclass(frozen=True)
class _Failure:
    careful: bool
    error: str


@dataclass(frozen=True)
class _Success:
    attempt: _Attempt
    cue_count: int


def _transcribe_checked(context: JobContext, planned: PlannedJob, wav: Path | None, problem: str | None) -> _Attempt:
    if wav is None:
        raise EngineError(problem or "audio could not be prepared")
    careful = planned.job.mode == "careful"
    first = _Attempt(_run(context, planned, wav, careful=careful), careful=careful)
    problems = find_problems(first.transcript)
    if careful or not problems:
        return first
    logger.info("re-running %s carefully: %s", planned.json_key, "; ".join(problems))
    try:
        return _Attempt(_run(context, planned, wav, careful=True), careful=True)
    except EngineError:
        logger.warning("careful re-run of %s failed; keeping the first result", planned.json_key, exc_info=True)
        return first


def _run(context: JobContext, planned: PlannedJob, wav: Path, *, careful: bool) -> Transcript:
    timeout_s = max(context.min_timeout_s, planned.audio.duration_s * JOB_TIMEOUT_PER_AUDIO_S)
    if careful:
        timeout_s *= CAREFUL_TIMEOUT_FACTOR
    language = planned.job.language
    return context.engine.transcribe(wav, language, careful=careful, timeout_s=timeout_s)


def _output(
    context: JobContext,
    planned: PlannedJob,
    started_at: datetime,
    outcome: _Failure | _Success,
    srt: bytes = b"",
) -> JobOutput:
    if isinstance(outcome, _Failure):
        status: JobStatus = "failed"
        careful, aligned, cue_count, error = outcome.careful, False, 0, outcome.error
    else:
        status = "ok" if outcome.cue_count else "no_speech"
        careful, aligned = outcome.attempt.careful, outcome.attempt.transcript.aligned
        cue_count, error = outcome.cue_count, None
    job_result = JobResult(
        batch_id=planned.batch_id,
        item_id=planned.manifest_item.item_id,
        language=planned.job.language,
        mode=planned.job.mode,
        run_id=context.run_id,
        status=status,
        error=error,
        aligned=aligned,
        cue_count=cue_count,
        audio_duration_s=planned.audio.duration_s,
        started_at=started_at,
        finished_at=context.now(),
        engine=context.engine.settings(careful=careful),
    )
    return JobOutput(job_result=job_result, srt=srt)
