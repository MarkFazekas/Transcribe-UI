"""Worker child process: discovers ready batches and transcribes them.

The pipeline (architecture §5.3) is not implemented yet; until then the child fails loudly so
the entry terminates the pod straight away.
"""

import logging
import sys

logger = logging.getLogger(__name__)


def main() -> int:
    """Run the pipeline and return the exit code."""
    logging.basicConfig(level=logging.INFO)
    logger.error("the transcription pipeline is not implemented yet")
    return 1


if __name__ == "__main__":
    sys.exit(main())
