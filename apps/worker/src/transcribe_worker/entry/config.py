"""Entry configuration read from the environment, with safe defaults (standard library only)."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Self

from transcribe_worker.entry.constants import (
    DEFAULT_API_BASE_URL,
    DEFAULT_MAX_RUNTIME_S,
    ENV_API_BASE_URL,
    ENV_API_KEY,
    ENV_KEEP_POD_ALIVE,
    ENV_MAX_RUNTIME_S,
    ENV_POD_ID,
    MAX_RUNTIME_CEILING_S,
    TRUE_VALUES,
)
from transcribe_worker.entry.pod_api import PodApi, RunpodPodApi

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EntryConfig:
    """Settings of the entry and the watchdog. The API key is kept out of `repr`."""

    pod_id: str | None
    api_key: str | None = field(repr=False)
    api_base_url: str
    keep_alive: bool
    max_runtime_s: int

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Self:
        """Read the configuration; invalid values fall back to safe defaults instead of failing."""
        return cls(
            pod_id=env.get(ENV_POD_ID) or None,
            api_key=env.get(ENV_API_KEY) or None,
            api_base_url=env.get(ENV_API_BASE_URL) or DEFAULT_API_BASE_URL,
            keep_alive=env.get(ENV_KEEP_POD_ALIVE, "").strip().lower() in TRUE_VALUES,
            max_runtime_s=_parse_max_runtime(env.get(ENV_MAX_RUNTIME_S)),
        )

    @property
    def on_runpod(self) -> bool:
        """Return True when running in a pod (Runpod always sets the pod id)."""
        return self.pod_id is not None

    @property
    def misconfigured(self) -> bool:
        """Return True in a pod that cannot terminate itself (pod id without API key)."""
        return self.on_runpod and self.api_key is None

    def pod_api(self) -> PodApi | None:
        """Return the API of the own pod, or None if it cannot be built."""
        if self.pod_id is None or self.api_key is None:
            return None
        try:
            return RunpodPodApi(self.api_base_url, self.api_key, self.pod_id)
        except ValueError:
            logger.exception("invalid %s; using the default", ENV_API_BASE_URL)
            return RunpodPodApi(DEFAULT_API_BASE_URL, self.api_key, self.pod_id)


def _parse_max_runtime(raw: str | None) -> int:
    if raw is None:
        return DEFAULT_MAX_RUNTIME_S
    try:
        seconds = int(raw)
    except ValueError:
        logger.warning("invalid %s=%r; using %d s", ENV_MAX_RUNTIME_S, raw, DEFAULT_MAX_RUNTIME_S)
        return DEFAULT_MAX_RUNTIME_S
    if seconds <= 0:
        logger.warning("non-positive %s; using %d s", ENV_MAX_RUNTIME_S, DEFAULT_MAX_RUNTIME_S)
        return DEFAULT_MAX_RUNTIME_S
    return min(seconds, MAX_RUNTIME_CEILING_S)
