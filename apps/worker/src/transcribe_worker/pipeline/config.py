"""Worker configuration read from the environment (the values are never logged)."""

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, PositiveFloat, PositiveInt, SecretStr, StringConstraints

from transcribe_contracts.constants import ULID_PATTERN
from transcribe_contracts.ids import new_ulid
from transcribe_worker.entry.config import EntryConfig
from transcribe_worker.pipeline import constants


class R2Settings(BaseModel):
    """Connection to the R2 bucket; secrets stay out of `repr` and logs."""

    model_config = ConfigDict(frozen=True)

    endpoint_url: HttpUrl
    bucket: Annotated[str, StringConstraints(min_length=1)]
    access_key_id: SecretStr
    secret_access_key: SecretStr


class WorkerConfig(BaseModel):
    """Settings of one worker run."""

    model_config = ConfigDict(frozen=True)

    run_id: Annotated[str, StringConstraints(pattern=ULID_PATTERN)]
    pod_id: Annotated[str, StringConstraints(min_length=1)]
    max_runtime_s: PositiveInt
    r2: R2Settings
    backup_dir: Path
    work_dir: Path
    audio_cache_gb: PositiveFloat
    engine_factory: str = Field(description="'module:callable' that builds the Transcriber.")
    job_timeout_min_s: PositiveFloat = Field(
        default=constants.JOB_TIMEOUT_MIN_S,
        description="Shortest per-job timeout (not read from the environment; tests lower it).",
    )

    @classmethod
    def from_env(cls, env: Mapping[str, str], now: datetime) -> Self:
        """Read the configuration.

        Raises:
            pydantic.ValidationError: A required value is missing or invalid (the message
                names the field, never its value).
            KeyError: The engine name is unknown.
        """
        entry = EntryConfig.from_env(env)
        return cls(
            run_id=env.get(constants.ENV_RUN_ID) or new_ulid(now),
            pod_id=entry.pod_id or constants.LOCAL_POD_ID,
            max_runtime_s=entry.max_runtime_s,
            r2=R2Settings.model_validate(
                {
                    "endpoint_url": env.get(constants.ENV_R2_ENDPOINT_URL),
                    "bucket": env.get(constants.ENV_R2_BUCKET),
                    "access_key_id": env.get(constants.ENV_R2_ACCESS_KEY_ID),
                    "secret_access_key": env.get(constants.ENV_R2_SECRET_ACCESS_KEY),
                },
            ),
            backup_dir=Path(env.get(constants.ENV_BACKUP_DIR) or constants.DEFAULT_BACKUP_DIR),
            work_dir=Path(env.get(constants.ENV_WORK_DIR) or constants.DEFAULT_WORK_DIR),
            audio_cache_gb=env.get(constants.ENV_AUDIO_CACHE_GB) or constants.DEFAULT_AUDIO_CACHE_GB,
            engine_factory=constants.ENGINE_FACTORIES[env.get(constants.ENV_ENGINE) or constants.DEFAULT_ENGINE],
        )

    @property
    def audio_cache_bytes(self) -> int:
        """Return the audio cache limit in bytes."""
        return int(self.audio_cache_gb * constants.BYTES_PER_GB)
