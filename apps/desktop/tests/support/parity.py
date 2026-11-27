"""Parity runner: the same requests through `Backend.execute` and through the CLI (plan m5 §1).

Each leg gets a fresh temp root and a fresh backend from the same seeded factory. The backend
leg validates and executes each request document directly; the CLI leg writes it to a file
and calls `main(["run", <file>])` in-process with the same wiring injected. Responses and
progress events are normalised (`<root>`, `/`, `<flavor>`) and compared step by step.
"""

import io
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from support.fakes import seeded_ports
from support.normalise import ROOT_PLACEHOLDER, PathNormaliser
from transcribe_ui.api.backend import Backend, Routes
from transcribe_ui.api.classes import ProgressEvent, Response
from transcribe_ui.api.schema import response_adapter
from transcribe_ui.cli.main import main
from transcribe_ui.cli.output import exit_code

# Builds the dispatch table of one leg from the leg's root (tests with stub handlers).
type RoutesFactory = Callable[[Path], Routes]


@dataclass(frozen=True)
class Exchange:
    """One request's normalised outcome."""

    response: str
    events: tuple[str, ...]
    exit_code: int

    def golden(self) -> str:
        """Return the exchange as the text of a golden file."""
        document = {
            "exit_code": self.exit_code,
            "response": json.loads(self.response),
            "events": [json.loads(event) for event in self.events],
        }
        return f"{json.dumps(document, indent=2, ensure_ascii=False)}\n"


@dataclass
class RecordingProgress:
    """Progress sink that keeps the events."""

    events: list[ProgressEvent] = field(default_factory=list)

    def emit(self, event: ProgressEvent) -> None:
        """Keep the event."""
        self.events.append(event)


@dataclass(frozen=True)
class Leg:
    """One side of a parity run: its root, its backend and its normaliser."""

    root: Path
    backend: Backend
    normaliser: PathNormaliser

    @classmethod
    def create(cls, root: Path, routes: RoutesFactory | None) -> Self:
        """Make the root and a backend from the seeded factory."""
        root.mkdir(parents=True)
        backend = Backend(seeded_ports(), None if routes is None else routes(root))
        return cls(root, backend, PathNormaliser([root]))

    def document(self, template: str) -> str:
        """Return the request document with `<root>` replaced by this leg's root (JSON-escaped)."""
        escaped = json.dumps(str(self.root))[1:-1]
        return template.replace(ROOT_PLACEHOLDER, escaped)

    def exchange(self, response: Response, events: Sequence[ProgressEvent], code: int) -> Exchange:
        """Return the normalised outcome."""
        rendered = tuple(self.normaliser.model_json(event) for event in events)
        return Exchange(self.normaliser.response_json(response), rendered, code)


def _via_backend(leg: Leg, template: str) -> Exchange:
    progress = RecordingProgress()
    response = leg.backend.execute_json(leg.document(template), progress)
    return leg.exchange(response, progress.events, exit_code(response))


def _via_cli(leg: Leg, template: str, request_file: Path) -> Exchange:
    request_file.write_bytes(leg.document(template).encode("utf-8"))
    stdout = io.BytesIO()
    stderr = io.BytesIO()
    code = main(["run", str(request_file)], make_backend=lambda: leg.backend, stdout=stdout, stderr=stderr)
    lines = stderr.getvalue().decode("utf-8").splitlines()
    events = [ProgressEvent.model_validate_json(line) for line in lines]
    return leg.exchange(response_adapter().validate_json(stdout.getvalue()), events, code)


def run_parity(tmp_path: Path, templates: Sequence[str], routes: RoutesFactory | None = None) -> list[Exchange]:
    """Run the request documents through both legs, assert they agree, return the CLI leg's outcomes.

    A document may contain `<root>`, which each leg replaces with its own root.
    """
    direct = Leg.create(tmp_path / "backend", routes)
    via_cli = Leg.create(tmp_path / "cli", routes)
    # Request files live outside the leg's root, so they never show up in what a request sees there.
    requests_dir = tmp_path / "requests-cli"
    requests_dir.mkdir()
    exchanges = []
    for index, template in enumerate(templates):
        expected = _via_backend(direct, template)
        actual = _via_cli(via_cli, template, requests_dir / f"request-{index}.json")
        assert actual == expected, f"request {index}: the CLI and the backend disagree"
        exchanges.append(actual)
    return exchanges


def run_one(tmp_path: Path, template: str, routes: RoutesFactory | None = None) -> Exchange:
    """Run one request document through both legs; return the CLI leg's outcome."""
    return run_parity(tmp_path, [template], routes)[0]
