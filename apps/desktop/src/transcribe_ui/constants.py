"""Constants of the desktop app as a whole."""

from typing import Final

# Name of the installed distribution; its metadata holds the app version.
DISTRIBUTION_NAME: Final = "transcribe-ui"
# Name of the top-level package; the parent of every logger of the app.
PACKAGE_NAME: Final = "transcribe_ui"
# File of the build stamp (`<base>+<YYYYMMDD>.<short sha>`), package data that only a release build has.
VERSION_STAMP_FILE: Final = "version.txt"
