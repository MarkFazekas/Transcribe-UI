"""The guards of `scripts/build_ffmpeg.py`: checksums, the out folder, the host, timeouts and the cache key."""

import hashlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

import pytest
from build_ffmpeg import KEYED_FILES, build_host_problem, cache_key, pins_output
from build_ffmpeg_fetch import DOWNLOAD_HEAD_BYTES, SERVED_FROM_SUFFIX, curl_command, fetch
from build_ffmpeg_pins import CACHE_SALT, MANYLINUX_IMAGE, Platform, Source
from build_ffmpeg_steps import BUILD_INFO, Unpacked, described, out_problem, output_of

# Encoding of the text files the tests write and read.
ENCODING = "utf-8"
# The bytes of the pinned tarball.
PINNED_BYTES = b"pinned source"
# What a bot-protection page serves instead.
HTML_PAGE = b"<!DOCTYPE html><html><body>Checking your browser</body></html>"
# A download that curl reports done but never writes.
NOTHING = b"nothing written"
# The primary URL and the fallback of the demo source.
PRIMARY = "https://primary.example.invalid/demo-1.0.tar.gz"
FALLBACK = "https://fallback.example.invalid/demo-1.0.tar.gz"
# A source pinned to those bytes, with a fallback URL.
PINNED = Source(
    name="demo",
    version="1.0",
    urls=(PRIMARY, FALLBACK),
    sha256=hashlib.sha256(PINNED_BYTES).hexdigest(),
    signing_key="0123456789ABCDEF0123456789ABCDEF01234567",
    licence="LICENSE",
)
# A program that runs far longer than the shortened timeout.
SLEEPER = (sys.executable, "-c", "import time; time.sleep(30)")


@dataclass
class FakeCurl:
    """Stands in for `run_step`: serves each URL its payload (None: curl fails) and records the URLs asked."""

    served: dict[str, bytes | None]
    asked: list[str] = field(default_factory=list)

    def __call__(self, argv: Sequence[str], *_: object) -> None:
        """Write what the URL (the last argument) serves where `--output` points, or fail like curl.

        `NOTHING` stands for a curl that exits 0 without writing the file.
        """
        url = argv[-1]
        self.asked.append(url)
        payload = self.served[url]
        if payload is None:
            msg = "curl failed (exit 22)"
            raise RuntimeError(msg)
        if payload is NOTHING:
            return
        target = Path(argv[list(argv).index("--output") + 1])
        target.write_bytes(payload)


@pytest.fixture(autouse=True)
def fake_curl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let `fetch` find a `curl` without one being installed."""
    monkeypatch.setattr("build_ffmpeg_fetch.tool", str)


def _serve(monkeypatch: pytest.MonkeyPatch, served: dict[str, bytes | None]) -> FakeCurl:
    curl = FakeCurl(served)
    monkeypatch.setattr("build_ffmpeg_fetch.run_step", curl)
    return curl


def _failure(folder: Path) -> str:
    """Return the error of a `fetch` of the demo source that must fail."""
    try:
        fetch(PINNED, folder)
    except RuntimeError as error:
        return str(error)
    pytest.fail("fetch did not fail")


def test_fetch_takes_the_first_good_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The primary URL serves the tarball: the fallback is never asked, and the URL is recorded."""
    curl = _serve(monkeypatch, {PRIMARY: PINNED_BYTES, FALLBACK: None})
    fetched = fetch(PINNED, tmp_path)
    assert (fetched.archive.read_bytes(), fetched.served_from) == (PINNED_BYTES, PRIMARY)
    assert curl.asked == [PRIMARY]


@pytest.mark.parametrize("primary", [HTML_PAGE, None, NOTHING])
def test_fetch_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, primary: bytes | None) -> None:
    """An HTML page, a curl failure or a missing file on the primary: the fallback serves the tarball."""
    curl = _serve(monkeypatch, {PRIMARY: primary, FALLBACK: PINNED_BYTES})
    fetched = fetch(PINNED, tmp_path)
    assert (fetched.archive.read_bytes(), fetched.served_from) == (PINNED_BYTES, FALLBACK)
    assert curl.asked == [PRIMARY, FALLBACK]


def test_fetch_fails_only_when_every_url_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each URL is named with what it served (hash, size, first bytes) or why curl failed; nothing is kept."""
    _serve(monkeypatch, {PRIMARY: HTML_PAGE, FALLBACK: None})
    lines = _failure(tmp_path).splitlines()
    wrong = hashlib.sha256(HTML_PAGE).hexdigest()
    head = HTML_PAGE[:DOWNLOAD_HEAD_BYTES]
    assert lines[1] == f"{PRIMARY}: SHA-256 {wrong}, {len(HTML_PAGE)} bytes starting {head!r}"
    assert lines[2] == f"{FALLBACK}: curl failed (exit 22)"
    assert not (tmp_path / PINNED.archive).exists()


def test_fetch_reuses_a_verified_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tarball already there with the pinned SHA-256 is used without downloading, with its recorded URL."""
    curl = _serve(monkeypatch, {PRIMARY: PINNED_BYTES})
    fetch(PINNED, tmp_path)
    again = fetch(PINNED, tmp_path)
    assert (again.archive, again.served_from) == (tmp_path / PINNED.archive, PRIMARY)
    assert curl.asked == [PRIMARY]


def _record(folder: Path) -> Path:
    """Return the file that records which URL served the demo tarball."""
    return folder / f"{PINNED.archive}{SERVED_FROM_SUFFIX}"


def test_fetch_replaces_a_stale_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tarball already there with another SHA-256 is downloaded afresh; its old record is replaced."""
    (tmp_path / PINNED.archive).write_bytes(b"stale")
    _record(tmp_path).write_text(f"{FALLBACK}\n", encoding=ENCODING)
    curl = _serve(monkeypatch, {PRIMARY: PINNED_BYTES})
    fetched = fetch(PINNED, tmp_path)
    assert (fetched.archive.read_bytes(), fetched.served_from) == (PINNED_BYTES, PRIMARY)
    assert _record(tmp_path).read_text(encoding=ENCODING) == f"{PRIMARY}\n"
    assert curl.asked == [PRIMARY]


@pytest.mark.parametrize(
    "record",
    [b"https://elsewhere.example.invalid/demo-1.0.tar.gz\n", b"\xff\xfe not text", None],
    ids=["url-outside-the-pin", "undecodable", "missing"],
)
def test_fetch_ignores_an_unusable_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record: bytes | None
) -> None:
    """A reused tarball whose record names no URL of the pin, cannot be read or is missing: the URL is unknown."""
    (tmp_path / PINNED.archive).write_bytes(PINNED_BYTES)
    if record is not None:
        _record(tmp_path).write_bytes(record)
    curl = _serve(monkeypatch, {})
    assert fetch(PINNED, tmp_path).served_from is None
    assert not curl.asked


def test_curl_is_bounded_and_https_only() -> None:
    """Only HTTPS, also after redirects, and every wait (connect, transfer, retries, a stall) has a limit."""
    command = curl_command("curl", PRIMARY, Path("demo.tar.gz"))
    pairs = dict(pairwise(command))
    assert (pairs["--proto"], pairs["--proto-redir"]) == ("=https", "=https")
    assert all(option in pairs for option in ("--connect-timeout", "--max-time", "--retry-max-time", "--speed-time"))
    assert command[-1] == PRIMARY


def test_out_folder_must_be_an_earlier_build(tmp_path: Path) -> None:
    """A missing, empty or earlier build's folder is replaced; one with other files or a file is refused."""
    build = tmp_path / "build"
    build.mkdir()
    (build / BUILD_INFO).write_text("{}", encoding=ENCODING)
    other = tmp_path / "other"
    other.mkdir()
    (other / "notes.txt").write_text("keep", encoding=ENCODING)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert out_problem(tmp_path / "missing") is None
    assert out_problem(empty) is None
    assert out_problem(build) is None
    assert "not an ffmpeg build" in str(out_problem(other))
    assert "is not a folder" in str(out_problem(other / "notes.txt"))


@pytest.mark.parametrize(
    ("target", "system", "machine", "allowed"),
    [
        ("windows-x64", "linux", "x86_64", True),
        ("windows-x64", "win32", "AMD64", False),
        ("linux-x64", "linux", "x86_64", True),
        ("linux-x64", "linux", "aarch64", False),
        ("macos-arm64", "darwin", "arm64", True),
        ("macos-x64", "darwin", "arm64", False),
        ("macos-x64", "freebsd14", "amd64", False),
    ],
)
def test_build_host(target: Platform, system: str, machine: str, *, allowed: bool) -> None:
    """Windows is cross-built on Linux; every other platform builds only on itself."""
    assert (build_host_problem(target, system, machine) is None) == allowed


def test_a_hanging_tool_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """A check program that does not finish becomes an error instead of a hung build."""
    monkeypatch.setattr("build_ffmpeg_steps.TOOL_TIMEOUT_S", 1)
    with pytest.raises(RuntimeError, match="did not finish"):
        output_of(SLEEPER)


def test_cache_key_covers_the_pinned_inputs(tmp_path: Path) -> None:
    """The key hashes the pins module, so a bumped salt (or image, or package) gives another key."""
    assert {"build_ffmpeg_pins.py", "build_platform.py"} <= {path.name for path in KEYED_FILES}
    pins = next(path for path in KEYED_FILES if path.name == "build_ffmpeg_pins.py")
    bumped = tmp_path / pins.name
    text = pins.read_text(encoding=ENCODING)
    bumped.write_text(
        text.replace(f'CACHE_SALT = "{CACHE_SALT}"', f'CACHE_SALT = "{CACHE_SALT}-bumped"'), encoding=ENCODING
    )
    others = [path for path in KEYED_FILES if path != pins]
    assert bumped.read_text(encoding=ENCODING) != text
    assert cache_key("linux-x64", [*others, bumped]) != cache_key("linux-x64", KEYED_FILES)


def test_pins_output_for_the_workflow() -> None:
    """The key, the build image and the packages, one `$GITHUB_OUTPUT` line each."""
    lines = pins_output("windows-x64").splitlines()
    assert lines[0].startswith("key=ffmpeg-9.1-windows-x64-")
    assert lines[1:] == [f"image={MANYLINUX_IMAGE}", "packages=gcc-mingw-w64-x86-64-win32 nasm pkg-config"]


def test_build_description_names_the_serving_url() -> None:
    """`ffmpeg-build.json` adds to each source the URL that served it, next to its pinned URLs."""
    unpacked = {
        "FFmpeg": Unpacked(Path("ffmpeg"), "https://ffmpeg.example.invalid/f.tar.xz"),
        "zlib": Unpacked(Path("zlib"), None),
    }
    description = described("linux-x64", unpacked)
    ffmpeg, zlib = description["ffmpeg"], description["zlib"]
    assert isinstance(ffmpeg, dict)
    assert isinstance(zlib, dict)
    assert (ffmpeg["served_from"], zlib["served_from"]) == ("https://ffmpeg.example.invalid/f.tar.xz", None)
    assert "urls" in zlib
