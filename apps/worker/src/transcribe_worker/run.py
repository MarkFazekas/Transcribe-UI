"""Worker child process (started by the entry): transcribe every ready batch, then exit.

Exit code 0 means the run ended normally, failed jobs included; 1 means it could not work
(configuration, storage or engine start). Either way the entry then terminates the pod.
"""

import logging
import os
import sys
from datetime import UTC, datetime

from pydantic import ValidationError

from transcribe_worker.engine.host import EngineProcess
from transcribe_worker.pipeline.audio import decode_to_wav
from transcribe_worker.pipeline.config import WorkerConfig
from transcribe_worker.pipeline.constants import ENGINE_START_TIMEOUT_S, EXIT_FAILED, LOG_FILE_NAME
from transcribe_worker.pipeline.logs import configure_logging
from transcribe_worker.pipeline.runner import WorkerPorts, run_worker
from transcribe_worker.pipeline.storage import S3ObjectStore, r2_client

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


def main() -> int:
    """Read the configuration from the environment and run the worker."""
    try:
        config = WorkerConfig.from_env(os.environ, utc_now())
    except ValidationError as error:
        logging.basicConfig(level=logging.INFO)
        logger.critical("invalid worker configuration; check: %s", _invalid_fields(error))
        return EXIT_FAILED
    except KeyError as error:
        logging.basicConfig(level=logging.INFO)
        logger.critical("unknown engine %s", error)
        return EXIT_FAILED
    configure_logging(config.work_dir / LOG_FILE_NAME)
    ports = WorkerPorts(
        store=S3ObjectStore(r2_client(config.r2), config.r2.bucket),
        engine=EngineProcess(config.engine_factory, ENGINE_START_TIMEOUT_S),
        decode=decode_to_wav,
        now=utc_now,
    )
    return run_worker(config, ports)


def _invalid_fields(error: ValidationError) -> str:
    """Return the names of the invalid fields; the values (possibly secrets) are left out."""
    details = error.errors(include_input=False, include_url=False)
    return ", ".join(_location(detail["loc"]) for detail in details)


def _location(parts: tuple[int | str, ...]) -> str:
    return ".".join(str(part) for part in parts)


if __name__ == "__main__":
    sys.exit(main())
