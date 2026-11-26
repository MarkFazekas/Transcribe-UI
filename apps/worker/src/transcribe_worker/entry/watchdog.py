"""Watchdog process: terminate the pod after MAX_RUNTIME_S, whatever the worker does.

Standard library only. It runs in its own session, started first by the entry, and covers a
hanging worker, a blocked entry and test mode (KEEP_POD_ALIVE), architecture §5.2.
"""

import logging
import os
import sys
import time
from collections.abc import Callable

from transcribe_worker.entry.config import EntryConfig
from transcribe_worker.entry.constants import LOG_FORMAT
from transcribe_worker.entry.pod_api import PodApi, PodApiError, terminate_until_gone

logger = logging.getLogger(__name__)

type Sleep = Callable[[float], None]


def watch(max_runtime_s: int, api: PodApi, sleep: Sleep = time.sleep) -> None:
    """Probe the pod, wait for the deadline, then terminate it (retrying until it is gone)."""
    try:
        if not api.exists():
            logger.critical("the API does not know this pod; termination will keep failing")
    except PodApiError as error:
        logger.warning("pod probe failed (%s)", error)
    logger.info("watchdog armed: terminating the pod in %d s", max_runtime_s)
    sleep(max_runtime_s)
    logger.warning("deadline reached: terminating the pod")
    terminate_until_gone(api, sleep)


def main() -> int:
    """Entry point of the watchdog process."""
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    config = EntryConfig.from_env(os.environ)
    api = config.pod_api()
    if api is None:
        logger.critical("watchdog without a pod API: nothing to watch")
        return 0
    watch(config.max_runtime_s, api)
    return 0


if __name__ == "__main__":
    sys.exit(main())
