"""Container entry (PID 1 under tini): start the watchdog, run the worker, always end the pod.

Standard library only (see the package docstring). Flow, architecture §5.2:
1. start the watchdog process (independent; terminates the pod after MAX_RUNTIME_S);
2. run the worker as a child process, killed at the same deadline;
3. when the child ends for any reason, terminate the pod and never exit while it exists
   (an exiting PID 1 would presumably make Runpod restart the container). With
   KEEP_POD_ALIVE=true wait instead, until the deadline or until the watchdog dies, then
   terminate. From step 3 on, termination signals are ignored so they cannot interrupt it.
"""

import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import FrameType
from typing import Protocol

from transcribe_worker.entry.config import EntryConfig
from transcribe_worker.entry.constants import (
    CHILD_MODULE,
    CHILD_TIMEOUT_EXIT_CODE,
    KEEP_ALIVE_POLL_S,
    LOG_FORMAT,
    MISCONFIGURED_SLEEP_S,
    SIGTERM_EXIT_CODE,
    WATCHDOG_MODULE,
)
from transcribe_worker.entry.pod_api import PodApi, PodApiError, terminate_until_gone

logger = logging.getLogger(__name__)

# Signals that would end the entry without running its `finally` block.
HANDLED_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


class Liveness(Protocol):
    """Tells whether a started process is still running."""

    def alive(self) -> bool:
        """Return True while the process runs."""


@dataclass(frozen=True)
class EntryPorts:
    """Side effects of the entry, injected so the flow can be tested without processes."""

    start_watchdog: Callable[[], Liveness]
    run_child: Callable[[float], int]
    sleep: Callable[[float], None]
    monotonic: Callable[[], float]
    shield_signals: Callable[[], None]
    wait_misconfigured: Callable[[], None]


def supervise(config: EntryConfig, api: PodApi | None, ports: EntryPorts) -> int:
    """Run the worker child and then end the pod; return the child's exit code.

    Outside Runpod the exit code is returned after the child ends. In a pod the function only
    returns once the pod is gone, which in practice never happens: termination kills the
    container first.
    """
    if config.misconfigured or (config.on_runpod and api is None):
        logger.critical("in a pod without a usable RUNPOD_API_KEY: cannot terminate; not starting")
        ports.wait_misconfigured()
        return 1
    deadline = ports.monotonic() + config.max_runtime_s
    watchdog: Liveness | None = None
    exit_code = 1
    try:  # noqa: WPS501 - no except on purpose: the pod ends whatever happens, errors propagate
        watchdog = _start_watchdog(ports)
        if api is None or _pod_is_known(api):
            exit_code = ports.run_child(max(deadline - ports.monotonic(), 0))
            logger.info("worker exited with code %d", exit_code)
    finally:
        ports.shield_signals()
        _end_pod(config, api, ports, watchdog, deadline)
    return exit_code


def _start_watchdog(ports: EntryPorts) -> Liveness | None:
    try:
        return ports.start_watchdog()
    except OSError:
        logger.exception("could not start the watchdog; the entry deadline still applies")
        return None


def _pod_is_known(api: PodApi) -> bool:
    try:
        known = api.exists()
    except PodApiError as error:
        logger.warning("pod probe failed (%s); continuing", error)
        return True
    if not known:
        logger.critical("the API does not know this pod: not starting the worker")
    return known


def _end_pod(
    config: EntryConfig,
    api: PodApi | None,
    ports: EntryPorts,
    watchdog: Liveness | None,
    deadline: float,
) -> None:
    if api is None:
        logger.warning("not on Runpod: nothing to terminate")
        return
    if config.keep_alive:
        logger.warning("KEEP_POD_ALIVE: keeping the pod until the deadline (%d s)", config.max_runtime_s)
        while ports.monotonic() < deadline and watchdog is not None and watchdog.alive():
            ports.sleep(KEEP_ALIVE_POLL_S)
    logger.info("terminating the pod")
    terminate_until_gone(api, ports.sleep)


@dataclass(frozen=True)
class ProcessLiveness:
    """Liveness of a started subprocess."""

    process: subprocess.Popen[bytes]

    def alive(self) -> bool:
        """Return True while the process runs."""
        return self.process.poll() is None


def run_with_timeout(argv: list[str], timeout_s: float) -> int:
    """Run a process; kill it and return CHILD_TIMEOUT_EXIT_CODE if it outlives the timeout."""
    try:
        return subprocess.run(argv, timeout=timeout_s, check=False).returncode  # noqa: S603 - fixed argv
    except subprocess.TimeoutExpired:
        logger.exception("worker killed at the deadline")
        return CHILD_TIMEOUT_EXIT_CODE


def _spawn_watchdog() -> Liveness:
    process = subprocess.Popen(  # noqa: S603 - fixed argv: this interpreter and a module of this package
        [sys.executable, "-m", WATCHDOG_MODULE],
        start_new_session=True,
    )
    logger.info("watchdog started (pid %d)", process.pid)
    return ProcessLiveness(process)


def _run_child(timeout_s: float) -> int:
    return run_with_timeout([sys.executable, "-m", CHILD_MODULE], timeout_s)


def _wait_misconfigured() -> None:
    while True:  # noqa: WPS457 - exiting would make Runpod restart the container; wait to be killed
        time.sleep(MISCONFIGURED_SLEEP_S)
        logger.critical("misconfigured pod still waiting to be terminated from outside")


def _exit_on_signal(_signum: int, _frame: FrameType | None) -> None:
    sys.exit(SIGTERM_EXIT_CODE)


def _shield_signals() -> None:
    for handled in HANDLED_SIGNALS:
        signal.signal(handled, signal.SIG_IGN)


def main() -> int:
    """Entry point of the container."""
    # First: a signal must never end the entry without running its `finally` block.
    for handled in HANDLED_SIGNALS:
        signal.signal(handled, _exit_on_signal)
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    config = EntryConfig.from_env(os.environ)
    ports = EntryPorts(
        start_watchdog=_spawn_watchdog,
        run_child=_run_child,
        sleep=time.sleep,
        monotonic=time.monotonic,
        shield_signals=_shield_signals,
        wait_misconfigured=_wait_misconfigured,
    )
    return supervise(config, config.pod_api(), ports)


if __name__ == "__main__":
    sys.exit(main())
