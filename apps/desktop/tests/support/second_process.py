"""Runs one request file through the CLI in a fresh process (the writer-lock test).

Usage: `python second_process.py <root> <request.json>`, with `apps/desktop/tests` on
`PYTHONPATH`. The local state lives under `<root>` as in the in-process tests, and
`get_app_info` is a database request (`support.routes.database_routes`). The CLI's response goes
to stdout and its exit code is the process's.
"""

import sys
from pathlib import Path

from support.fakes import seeded_ports
from support.routes import database_routes
from transcribe_ui.api.backend import Backend
from transcribe_ui.cli.main import main


def run() -> int:
    """Run the request against the root given on the command line."""
    root, request_file = sys.argv[1:]
    backend = Backend(seeded_ports(Path(root)), database_routes())
    return main(["run", request_file], make_backend=lambda: backend)


if __name__ == "__main__":
    sys.exit(run())
