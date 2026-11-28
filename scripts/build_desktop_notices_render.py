"""The text of the third-party notices (docs/ai/plans/m8.md, M8.3).

Used by `scripts/build_desktop_notices.py`: a header with the list of the entries, then each
entry with its licence texts, a text repeated being referred to. Standard library only.
"""

import hashlib
from collections.abc import Sequence

from build_desktop_licences import PROJECT_URL
from build_desktop_notices_classes import ENCODING, Notice

# Width of the line between two entries of the notices.
ENTRY_RULE_WIDTH = 78
# The line between two entries of the notices.
ENTRY_RULE = "=" * ENTRY_RULE_WIDTH


def _summary(notice: Notice) -> str:
    return f"  - {notice.name} {notice.version} ({notice.expression})"


def _header(title: str, notices: Sequence[Notice]) -> list[str]:
    return [
        "THIRD-PARTY SOFTWARE NOTICES",
        title,
        "",
        f"Transcribe-UI is licensed under the Apache License 2.0; its source code is at {PROJECT_URL}.",
        "This executable also contains the third-party software listed below, each under its own",
        "licence. The list was made from the files this executable contains; each entry gives the",
        "version, the licence and the full licence texts.",
        "",
        "Contents:",
        *map(_summary, notices),
    ]


def _entry(notice: Notice, seen: dict[str, str]) -> list[str]:
    lines = ["", ENTRY_RULE, f"{notice.name} {notice.version}"]
    lines.append(f"Licence: {notice.expression}")
    if notice.note:
        lines.append(notice.note)
    for licence in notice.licences:
        digest = hashlib.sha256(licence.text.encode(ENCODING)).hexdigest()
        lines.extend(("", f"----- {licence.title} -----"))
        earlier = seen.get(digest)
        if earlier is not None:
            lines.append(f"(The same text as {earlier} above.)")
            continue
        seen[digest] = f"{licence.title} of {notice.name}"
        lines.append(licence.text)
    return lines


def _order(notice: Notice) -> tuple[str, str]:
    return notice.name.casefold(), notice.version


def render(title: str, notices: Sequence[Notice]) -> str:
    """Return the text of the notices: a header with the contents, then each entry by name; a text repeated is referred to."""
    ordered = sorted(notices, key=_order)
    lines = _header(title, ordered)
    seen: dict[str, str] = {}
    for notice in ordered:
        lines.extend(_entry(notice, seen))
    return "\n".join([*lines, ""])
