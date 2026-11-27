"""Command line of the pod reaper (`transcribe-worker-reaper`)."""

import argparse
import logging
import os
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from transcribe_worker.entry.constants import DEFAULT_API_BASE_URL
from transcribe_worker.reaper.api import ReaperApi, RunpodReaperApi
from transcribe_worker.reaper.constants import ENV_API_BASE_URL, ENV_API_KEY, EXIT_FAILED, EXIT_OK, LOG_FORMAT
from transcribe_worker.reaper.reap import ReaperPorts, reap

logger = logging.getLogger(__name__)


def build_api(env: Mapping[str, str]) -> ReaperApi | None:
    """Return the API client from the environment, or None (logged) if it cannot be built."""
    api_key = env.get(ENV_API_KEY, "").strip()
    if not api_key:
        logger.error("%s is not set", ENV_API_KEY)
        return None
    try:
        return RunpodReaperApi(env.get(ENV_API_BASE_URL) or DEFAULT_API_BASE_URL, api_key)
    except ValueError as error:
        logger.error("%s", error)  # noqa: TRY400 - message only, no traceback
        return None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one pass; exit 1 on any failure or malformed app pod name."""
    parser = argparse.ArgumentParser(description="Terminate overdue transcribe-ui pods on Runpod.")
    parser.add_argument("--dry-run", action="store_true", help="only report what would be done")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    api = build_api(os.environ)
    if api is None:
        return EXIT_FAILED
    ports = ReaperPorts(now=_utc_now, sleep=time.sleep)
    ok = reap(api, ports, dry_run=args.dry_run)
    return EXIT_OK if ok else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
