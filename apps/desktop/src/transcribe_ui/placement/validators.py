"""Validators of the download requests' fields (architecture §3.5)."""

from pathlib import Path


def check_absolute_file(path: str) -> str:
    """Return the path of a file read or written on this computer, which must be absolute (a request validator).

    This computer's rule decides: a relative path would depend on the frontend's working folder.

    Raises:
        ValueError: The path is not absolute.
    """
    if not Path(path).is_absolute():
        msg = "the path must be absolute"
        raise ValueError(msg)
    return path
