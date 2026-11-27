"""Typed Runpod REST v2 client (architecture §3.7).

Pods, queued pods, templates, secrets, Global Volumes and the GPU catalog. Every method raises
`RunpodApiError` on failure, and a create may raise `AmbiguousCreateError` (the retry policy is
in `runpod/retry.py`).
"""

import time
from types import TracebackType
from typing import Self

import httpx
from pydantic import SecretStr

from transcribe_ui.runpod.account import CatalogApi, SecretsApi, TemplatesApi, VolumesApi
from transcribe_ui.runpod.constants import API_BASE_URL
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runpod.session import RunpodHttp, Sleep


class RunpodClient:
    """The Runpod API as the desktop app uses it; close it after use (a context manager)."""

    def __init__(
        self,
        api_key: SecretStr,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Sleep = time.sleep,
        base_url: str = API_BASE_URL,
    ) -> None:
        """Create the client (see `RunpodHttp` for the arguments)."""
        self._http = RunpodHttp(api_key, base_url=base_url, transport=transport, sleep=sleep)
        self.pods = PodsApi(self._http)
        self.templates = TemplatesApi(self._http)
        self.secrets = SecretsApi(self._http)
        self.volumes = VolumesApi(self._http)
        self.catalog = CatalogApi(self._http)

    def __enter__(self) -> Self:
        """Return the client."""
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        """Close the connections."""
        self._http.close()
