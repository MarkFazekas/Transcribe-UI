"""Process-local cache of the latest scans, reused by `preview_selection` (architecture §3.2).

The GUI previews after every click; reusing the scan it shows avoids rescanning a large tree
each time and keeps the preview consistent with the tree on screen. A one-shot CLI process
starts empty, so its previews always rescan.
"""

import threading
from collections import OrderedDict

from transcribe_ui.discovery.classes import ScanSpec, ScanTreeResponse
from transcribe_ui.discovery.constants import SCAN_CACHE_SIZE


def _options(spec: ScanSpec) -> ScanSpec:
    """Return only the scan options of a spec (a `scan_tree` request carries its action too)."""
    return ScanSpec.model_validate(spec.model_dump(include=set(ScanSpec.model_fields)))


class ScanCache:
    """The latest scans by `scan_id`, with the options they were made with; thread-safe."""

    def __init__(self, size: int = SCAN_CACHE_SIZE) -> None:
        """Keep at most `size` scans; the oldest one goes first."""
        self._size = size
        self._lock = threading.Lock()
        self._scans: OrderedDict[str, tuple[ScanSpec, ScanTreeResponse]] = OrderedDict()

    def put(self, spec: ScanSpec, tree: ScanTreeResponse) -> None:
        """Keep a scan made with these options."""
        with self._lock:
            self._scans[tree.scan_id] = (_options(spec), tree)
            while len(self._scans) > self._size:
                self._scans.popitem(last=False)

    def get(self, scan_id: str, spec: ScanSpec) -> ScanTreeResponse | None:
        """Return the kept scan with this id if it was made with the same options, else None."""
        with self._lock:
            found = self._scans.get(scan_id)
        if found is None or found[0] != _options(spec):
            return None
        return found[1]
