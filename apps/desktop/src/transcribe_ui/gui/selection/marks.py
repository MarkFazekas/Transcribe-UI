"""What a `preview_selection` answer says about each row of the tree (architecture §3.2).

Qt-free. The tick boxes come only from the latest preview: a file is ticked when the answer
lists it, a folder is ticked or partly ticked as the answer's folder state says, and anything
the answer does not name is unticked. Nothing is derived from the rows below. The same reading
serves the previews of what the active filters match.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from transcribe_ui.discovery.classes import FolderState, PreviewSelectionResponse
from transcribe_ui.gui.selection.constants import CHECKED, PARTIAL, UNCHECKED, CheckMark

# The tick box of each folder state of a preview.
FOLDER_MARKS: Mapping[FolderState, CheckMark] = MappingProxyType({"all": CHECKED, "partial": PARTIAL})


@dataclass(frozen=True)
class Marks:
    """The files a preview selected and the state of each folder with a selected file below it."""

    files: frozenset[str] = frozenset()
    folders: Mapping[str, FolderState] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def of(cls, preview: PreviewSelectionResponse) -> "Marks":
        """Read a preview's answer."""
        files = frozenset(selected.path for selected in preview.files)
        folders = MappingProxyType({folder.path: folder.state for folder in preview.folders})
        return cls(files, folders)

    def file_mark(self, path: str) -> CheckMark:
        """Return the tick box of a file."""
        return CHECKED if path in self.files else UNCHECKED

    def folder_mark(self, path: str) -> CheckMark:
        """Return the tick box of a folder."""
        state = self.folders.get(path)
        return UNCHECKED if state is None else FOLDER_MARKS[state]

    def mark(self, path: str, *, folder: bool) -> CheckMark:
        """Return the tick box of a folder or a file."""
        return self.folder_mark(path) if folder else self.file_mark(path)
