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
    """Return the media tree below the request's root."""
    return scan(request, context.ports.path_rules, context, request.action)


def preview_selection(request: PreviewSelectionRequest, context: RequestContext) -> PreviewSelectionResponse:
    """Scan, then return the files the request's operations select."""
    rules = context.ports.path_rules
    tree = scan(request.scan, rules, context, request.action)
    return select(tree, request.operations, rules)
