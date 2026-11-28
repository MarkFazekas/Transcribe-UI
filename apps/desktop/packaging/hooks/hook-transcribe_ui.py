"""PyInstaller hook of the `transcribe_ui` package (docs/ai/plans/m8.md §1 "What goes into each build").

PyInstaller loads this file by its path from `--additional-hooks-dir` (hence its name and the
missing `__init__.py`). `scripts/build_desktop.py` names the variant in
`TRANSCRIBE_UI_BUNDLE_VARIANT`; a build without it fails here.

- Every submodule is collected: the GUI entry imports `transcribe_ui.gui.app` through
  `importlib`, and the Alembic environment and revisions are loaded as files, so the analysis
  follows their imports only this way. The CLI variant leaves out `transcribe_ui.gui`.
- Data: the compiled catalogs (`locale/**/*.mo`; not the `.po` and `.pot` sources) and the
  Alembic environment with its `.py` files (`state/migrations`), which Alembic reads from the
  folder at run time (`state/upgrade.py`).
- GUI variant only: the window icon PNGs (`gui/icons/*.png`, `gui/icon.py`); the CLI variant
  contains no `transcribe_ui/gui/` data.
"""

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Environment variable naming the variant being built (`cli` or `gui`).
VARIANT_VARIABLE = "TRANSCRIBE_UI_BUNDLE_VARIANT"
# The variant with the GUI; every other variant leaves `transcribe_ui.gui` out.
GUI_VARIANT = "gui"
# The GUI package, which only the GUI variant contains.
GUI_PACKAGE = "transcribe_ui.gui"
# Package data the executables read at run time (paths relative to the package).
DATA_INCLUDES = ("locale/**/*.mo", "state/migrations")
# Package data of the GUI variant only (paths relative to the package).
GUI_DATA_INCLUDES = ("gui/icons/*.png",)
# Never bundled: bytecode caches of the data folders.
DATA_EXCLUDES = ("**/__pycache__",)

with_gui = os.environ[VARIANT_VARIABLE] == GUI_VARIANT


def _in_bundle(module_name: str) -> bool:
    if with_gui:
        return True
    return module_name != GUI_PACKAGE and not module_name.startswith(f"{GUI_PACKAGE}.")


hiddenimports = collect_submodules("transcribe_ui", filter=_in_bundle)
datas = collect_data_files(
    "transcribe_ui",
    include_py_files=True,
    includes=[*DATA_INCLUDES, *(GUI_DATA_INCLUDES if with_gui else ())],
    excludes=list(DATA_EXCLUDES),
)
