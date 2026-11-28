"""Downloading the pinned sources of an ffmpeg build (docs/ai/plans/m8.md §1 "ffmpeg").

Used by `scripts/build_ffmpeg.py`. A source's URLs are tried in order until one serves the
pinned SHA-256; anything else is deleted. Every curl wait is bounded, only HTTPS is followed,
and the URL that served a tarball is recorded next to it (`<archive>.url`) for the build
description. Standard library only.
"""

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

from build_ffmpeg_pins import Source
from build_ffmpeg_steps import run_step, tool

# Bytes of a wrong download shown in the error (enough to tell an HTML page).
DOWNLOAD_HEAD_BYTES = 32
# What curl prints after each download: status, content type and size (curl reads the `\n`;
# the final URL is left out, since a redirect to a release asset carries a signed query).
CURL_REPORT = r"HTTP %{http_code}, %{content_type}, %{size_download} bytes\n"
# Seconds one download attempt may take (the FFmpeg tarball is 12 MB).
DOWNLOAD_TIMEOUT_S = 300
# Seconds curl may spend on one URL's retries; with the attempt limit a URL takes at most about
# ten minutes, so a stalled primary leaves the fallback time within the job's hour.
RETRY_WINDOW_S = 300
# Seconds a connection may take to open.
CONNECT_TIMEOUT_S = 30
# A transfer slower than this many bytes per second for `SLOW_WINDOW_S` is given up.
SLOW_BYTES_PER_S = 1024
SLOW_WINDOW_S = 60
# Suffix of the file that records which URL served a downloaded tarball.
SERVED_FROM_SUFFIX = ".url"
# Bytes read at a time when hashing.
HASH_CHUNK_BYTES = 1024 * 1024


def sha256_of(path: Path) -> str:
    """Return the SHA-256 of a file in hex."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def describe_download(archive: Path) -> str:
    """Return the size and the first bytes of a download, so a wrong one shows what it was (an HTML page)."""
    with archive.open("rb") as stream:
        head = stream.read(DOWNLOAD_HEAD_BYTES)
    return f"{archive.stat().st_size} bytes starting {head!r}"


def curl_command(curl: str, url: str, archive: Path) -> list[str]:
    """Return the curl command that downloads `url` to `archive`: HTTPS only, every wait bounded."""
    command = [curl, "--fail", "--location", "--silent", "--show-error", "--proto", "=https", "--proto-redir", "=https"]
    command += ["--connect-timeout", str(CONNECT_TIMEOUT_S), "--max-time", str(DOWNLOAD_TIMEOUT_S)]
    command += ["--speed-limit", str(SLOW_BYTES_PER_S), "--speed-time", str(SLOW_WINDOW_S)]
    command += ["--retry", "3", "--retry-max-time", str(RETRY_WINDOW_S), "--write-out", CURL_REPORT]
    command += ["--output", str(archive), url]
    return command


def _checked(url: str, archive: Path, sha256: str) -> str | None:
    """Return what is wrong with a finished download (None: it is the pinned tarball)."""
    try:
        found = sha256_of(archive)
    except OSError as error:
        return f"{url}: the download cannot be read: {error}"
    if found == sha256:
        return None
    return f"{url}: SHA-256 {found}, {describe_download(archive)}"


def download(url: str, archive: Path, sha256: str) -> str | None:
    """Download `url` to `archive`; return None when it has the SHA-256, else what went wrong.

    Anything but the pinned tarball is deleted. curl prints the HTTP status, the content type
    and the size of every download to the log.
    """
    try:
        run_step(curl_command(tool("curl"), url, archive), archive.parent)
    except RuntimeError as error:
        problem: str | None = f"{url}: {error}"
    else:
        problem = _checked(url, archive, sha256)
    if problem is not None:
        archive.unlink(missing_ok=True)
    return problem


@dataclass(frozen=True)
class Fetched:
    """A verified source tarball and the URL that served it (None: unknown, an older copy was reused)."""

    archive: Path
    served_from: str | None


def _served_from(archive: Path) -> Path:
    return archive.with_name(f"{archive.name}{SERVED_FROM_SUFFIX}")


def _reused(archive: Path, sha256: str) -> Fetched | None:
    """Return a verified copy already downloaded (with the URL recorded next to it), or None."""
    if not archive.is_file() or sha256_of(archive) != sha256:
        return None
    record = _served_from(archive)
    if not record.is_file():
        return Fetched(archive, None)
    served = record.read_text(encoding="utf-8").strip()
    return Fetched(archive, served or None)


def fetch(source: Source, downloads: Path) -> Fetched:
    """Return the source tarball: a verified copy already there, or the first URL that serves the pinned SHA-256.

    A copy with another SHA-256 is replaced by a fresh download.

    Raises:
        RuntimeError: No URL served the pinned tarball (each one is named with what it served).
    """
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / source.archive
    reused = _reused(archive, source.sha256)
    if reused is not None:
        return reused
    problems: list[str] = []
    for url in source.urls:
        problem = download(url, archive, source.sha256)
        if problem is None:
            _served_from(archive).write_text(f"{url}\n", encoding="utf-8")
            return Fetched(archive, url)
        sys.stdout.write(f"Not the pinned {source.archive}: {problem}\n")
        problems.append(problem)
    listed = "\n".join(problems)
    msg = f"no source of {source.archive} served the pinned SHA-256 {source.sha256}:\n{listed}"
    raise RuntimeError(msg)
