"""Normalisation of responses and progress events for golden files (plan m5 §1 "Golden files").

Only fields typed `LocalPath` or `PathFlavor` are touched: the models' serializers call the
rewrite functions passed in the serialization context.
"""

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from transcribe_ui.api.classes import Response
from transcribe_ui.api.constants import LOCAL_PATH_CONTEXT_KEY, PATH_FLAVOR_CONTEXT_KEY
from transcribe_ui.api.schema import response_adapter

# Replacement of a leg's temp root.
ROOT_PLACEHOLDER = "<root>"
# Replacement of the host's path flavour.
FLAVOR_PLACEHOLDER = "<flavor>"


def _same_prefix(text: str, prefix: str) -> bool:
    # Windows paths compare case-insensitively (drive letters come in either case).
    return os.path.normcase(text[: len(prefix)]) == os.path.normcase(prefix)


class PathNormaliser:
    """Rewrites paths under the given roots to `<root>/...` with `/` separators."""

    def __init__(self, roots: Iterable[Path]) -> None:
        """Accept each root both as created and as resolved (symlinks, Windows 8.3 short names)."""
        forms = {form for root in roots for form in (str(root), os.path.realpath(root))}
        # Longest first, so a root inside another root wins.
        self._prefixes = sorted(forms, key=len, reverse=True)

    def path(self, path: str) -> str:
        """Return the normalised form of one path."""
        for prefix in self._prefixes:
            cut = len(prefix)
            rest = path[cut:]
            if _same_prefix(path, prefix) and (not rest or rest[0] in {"/", "\\"}):
                return f"{ROOT_PLACEHOLDER}{rest}".replace("\\", "/")
        return path.replace("\\", "/")

    def context(self) -> dict[str, Any]:
        """Return the serialization context that applies this normaliser."""
        return {LOCAL_PATH_CONTEXT_KEY: self.path, PATH_FLAVOR_CONTEXT_KEY: _flavor}

    def response_json(self, response: Response) -> str:
        """Return a response as normalised, indented JSON."""
        return response_adapter().dump_json(response, indent=2, context=self.context()).decode("utf-8")

    def model_json(self, model: BaseModel) -> str:
        """Return a model (a progress event) as normalised, compact JSON."""
        return model.model_dump_json(context=self.context())


def _flavor(_flavor_value: str) -> str:
    return FLAVOR_PLACEHOLDER
