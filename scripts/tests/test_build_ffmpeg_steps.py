"""The guards of `scripts/build_ffmpeg.py`: checksums, the out folder, the host, timeouts and the cache key."""

import hashlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from build_ffmpeg import KEYED_FILES, build_host_problem, cache_key, pins_output
from build_ffmpeg_pins import CACHE_SALT, MANYLINUX_IMAGE, Platform, Source
from build_ffmpeg_steps import BUILD_INFO, fetch, out_problem, output_of

# The bytes the fake download writes.
PINNED_BYTES = b"pinned source"
# A source pinned to those bytes.
PINNED = Source(
    name="demo",
    version="1.0",
    url="https://example.invalid/demo-1.0.tar.gz",
    sha256=hashlib.sha256(PINNED_BYTES).hexdigest(),
    signing_key="0123456789ABCDEF0123456789ABCDEF01234567",
    licence="LICENSE",
)
# A program that runs far longer than the shortened timeout.
SLEEPER = (sys.executable, "-c", "import time; time.sleep(30)")


@dataclass(frozen=True)
class FakeDownload:
    """Stands in for `run_step`: writes `payload` where curl's `--output` points; None fails the test."""

    payload: bytes | None

    def __call__(self, argv: Sequence[str], *_: object) -> None:
        """Write the payload as the download, or fail when nothing may be downloaded."""
        if self.payload is None:
            pytest.fail("nothing must be downloaded")
        target = Path(argv[list(argv).index("--output") + 1])
        target.write_bytes(self.payload)


@pytest.fixture(autouse=True)
def fake_curl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let `fetch` find a `curl` without one being installed."""
    monkeypatch.setattr("build_ffmpeg_steps.tool", str)


def test_fetch_refuses_and_deletes_a_wrong_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bytes with another SHA-256 fail the build, and the bad tarball is not kept."""
    monkeypatch.setattr("build_ffmpeg_steps.run_step", FakeDownload(b"tampered"))
    with pytest.raises(RuntimeError, match="SHA-256"):
        fetch(PINNED, tmp_path)
    assert not (tmp_path / PINNED.archive).exists()


def test_fetch_keeps_a_verified_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh download with the pinned SHA-256 is returned."""
    monkeypatch.setattr("build_ffmpeg_steps.run_step", FakeDownload(PINNED_BYTES))
    assert fetch(PINNED, tmp_path).read_bytes() == PINNED_BYTES


def test_fetch_reuses_a_verified_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tarball already there with the pinned SHA-256 is used without downloading."""
    (tmp_path / PINNED.archive).write_bytes(PINNED_BYTES)
    monkeypatch.setattr("build_ffmpeg_steps.run_step", FakeDownload(None))
    assert fetch(PINNED, tmp_path) == tmp_path / PINNED.archive


def test_out_folder_must_be_an_earlier_build(tmp_path: Path) -> None:
    """A missing, empty or earlier build's folder is replaced; one with other files or a file is refused."""
    build = tmp_path / "build"
    build.mkdir()
    (build / BUILD_INFO).write_text("{}", encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    (other / "notes.txt").write_text("keep", encoding="utf-8")
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
    pins = next(path for path in KEYED_FILES if path.name == "build_ffmpeg_pins.py")
    bumped = tmp_path / pins.name
    text = pins.read_text(encoding="utf-8")
    bumped.write_text(
        text.replace(f'CACHE_SALT = "{CACHE_SALT}"', f'CACHE_SALT = "{CACHE_SALT}-bumped"'), encoding="utf-8"
    )
    others = [path for path in KEYED_FILES if path != pins]
    assert bumped.read_text(encoding="utf-8") != text
    assert cache_key("linux-x64", [*others, bumped]) != cache_key("linux-x64", KEYED_FILES)


def test_pins_output_for_the_workflow() -> None:
    """The key, the build image and the packages, one `$GITHUB_OUTPUT` line each."""
    lines = pins_output("windows-x64").splitlines()
    assert lines[0].startswith("key=ffmpeg-9.1-windows-x64-")
    assert lines[1:] == [f"image={MANYLINUX_IMAGE}", "packages=gcc-mingw-w64-x86-64-win32 nasm pkg-config"]
