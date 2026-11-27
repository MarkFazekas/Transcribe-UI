"""Handlers of `scan_tree` and `preview_selection`; neither opens the database (architecture §3.2)."""

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.discovery.classes import (
    PreviewSelectionRequest,
    PreviewSelectionResponse,
    ScanTreeRequest,
    ScanTreeResponse,
)
from transcribe_ui.discovery.scan import scan
from transcribe_ui.discovery.selection import select


def scan_tree(request: ScanTreeRequest, context: RequestContext) -> ScanTreeResponse:
    """Return the media tree below the request's root and keep it for previews."""
    tree = scan(request, context, request.action)
    context.ports.scan_cache.put(request, tree)
    return tree


def preview_selection(request: PreviewSelectionRequest, context: RequestContext) -> PreviewSelectionResponse:
    """Return the files the request's operations select, on the kept scan `scan_id` or a new one."""
    cache = context.ports.scan_cache
    scan_id = request.scan_id
    tree = None if scan_id is None else cache.get(scan_id, request.scan)
    if tree is None:
        tree = scan(request.scan, context, request.action)
        cache.put(request.scan, tree)
    return select(tree, request.operations, context)
