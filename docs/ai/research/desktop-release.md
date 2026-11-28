# Desktop release facts (2026-11-28)

Facts for M8 (`plans/m8.md`) that outlive the plan. Legend: [V] verified from the source given;
[I] inference or prior knowledge, verify in the part that relies on it. Earlier packaging research
(toolkit, PyInstaller vs Nuitka, keyring in a bundle, ffmpeg sources): `desktop-packaging.md`.

## PyInstaller

- [V] Latest release 6.22.3 (2026-09-12); no 7.0 yet. Python 3.13 supported since 6.10.0, 3.14
  since 6.15.0 (https://pyinstaller.org/en/stable/CHANGES.html).
- [V] 6.13.0 (2025-04-15): "Onefile in combination with macOS's .app bundles will be blocked in
  v7.0"; the changelog calls such bundles "not really single file" and recommends onedir for a
  `.app` (same source).
- [V] `PYINSTALLER_RESET_ENVIRONMENT` was introduced in 6.10.0 (2024-08-10; the changelog lists
  it under Features, Incompatible Changes and Bootloader): a process that starts
  `sys.executable` to outlive itself (a restart) must set it (same source). The "Common issues
  and pitfalls" page says "introduced in PyInstaller 6.9"
  (https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html); the changelog's 6.9.0
  entry has nothing about it, so 6.10 is taken as right. `tooling.md` and
  `gui/settings/constants.py` still say 6.9; the M8.1 PR corrects them.
- [I] A `.png` icon is converted to `.ico`/`.icns` when Pillow is installed in the build
  environment.
- [I] The Qt hooks collect Qt's own translations (`PySide6/Qt/translations/qtbase_*.qm`) with
  QtCore; M8's bundle check confirms it per build.
- [I] `pyi-archive_viewer` (PyInstaller 6: `--list`, `--recursive`, `--brief`) and
  `PyInstaller.archive.readers.CArchiveReader` list the contents of a one-file executable.
- [I] Modules imported dynamically (`importlib.import_module`) are invisible to the analysis and
  need `--hidden-import` or `--collect-submodules`; data read through `importlib.resources` must
  be collected as data (`.py` files included where a tool reads them as files, as Alembic does
  with its revisions).

## PySide6 wheels

- [V] `pyside6-essentials` 6.12.0 (2026-10-08; the version in `uv.lock`) wheels:
  `cp310-abi3-manylinux_2_34_x86_64`, `manylinux_2_39_aarch64`, `macosx_14_0_universal2`,
  `win_amd64`, `win_arm64` (https://pypi.org/project/PySide6-Essentials/#files). So the Linux
  x86_64 floor is glibc 2.34 and the macOS floor is macOS 14.

## GitHub-hosted runners

- [V] Ubuntu 22.04 images (`ubuntu-22.04`, `ubuntu-22.04-arm`): deprecation from 2026-09-17,
  retirement (fully unsupported) 2027-04-17, brownouts 14:00-24:00 UTC on March 23, March 30,
  April 6 and April 13 (2027 [I]: the announcement gives no year)
  (https://github.com/actions/runner-images/issues/14254).
- [V] `ubuntu-26.04` is generally available; `ubuntu-latest` moves from 24.04 to 26.04 between
  2026-10-19 and 2026-11-19; pin `ubuntu-24.04` to stay
  (https://github.blog/changelog/2026-09-17-ubuntu-26-generally-available-and-latest-migration).
- [V] `macos-latest` moves from macOS 15 to macOS 26 from 2026-06-15 (30 days); pin `macos-15`
  to stay. `windows-2025` (and `windows-latest`) use Visual Studio 2026 from 2026-06-15
  (https://github.blog/changelog/2026-05-14-github-actions-upcoming-image-migrations).
- [V] x86_64 macOS runners: `macos-15-intel` is the Intel label; GitHub ends x86_64 macOS
  support when the macOS 15 image retires in fall 2027
  (https://github.blog/changelog/2025-09-19-github-actions-macos-13-runner-image-is-closing-down/);
  a secondary source says August 2027 [I]. `macos-14` is being retired
  (https://github.blog/changelog/2026-10-01-github-actions-macos-14-runner-image-retirement).
- [I] macOS and Windows hosted runners have a logged-in desktop session, so a Qt window can be
  shown with the native platform plugin (`cocoa`, `windows`); Linux needs Xvfb for `xcb`.

## GitHub Actions

- [V] `actions/cache` v6: `enableCrossOsArchive` (off by default) lets Windows runners restore a
  cache saved on another OS and the reverse; `fail-on-cache-miss`; `lookup-only` checks for an
  entry without downloading it. 10 GB per repository, evicted by last access, and entries not
  accessed for a week are evicted. A cache is scoped to its key, version and branch; the
  default branch's caches are available to other branches (https://github.com/actions/cache).
- [V] `actions/upload-artifact` v7 `archive: false` uploads one file as is (see
  `desktop-packaging.md` §8).
- [I] `gh release create <tag> <files>` creates a draft, uploads the files, then publishes, so
  a failed upload leaves a draft and no tag.

## FFmpeg

- [V] Latest release 9.1 "Gauss" (2026-11-20); source tarballs at
  `https://ffmpeg.org/releases/ffmpeg-<version>.tar.xz` with a PGP signature (`.asc`; 9.1 also
  a post-quantum `.pq.asc`); the download page lists no checksums
  (https://ffmpeg.org/download.html).
- [I] A build with no `--enable-gpl`/`--enable-nonfree` and `--disable-autodetect` uses only
  FFmpeg's own components and is LGPL 2.1 or later; `ffmpeg -L` prints the licence. Its native
  decoders cover the app's codecs (AAC, AC-3, E-AC-3, DTS, MP3, Opus, Vorbis, FLAC, TrueHD, PCM)
  and its native text subtitle decoders and SRT encoder cover extraction.
- [I] Windows builds are commonly cross-compiled on Linux with mingw-w64
  (`--target-os=mingw32 --cross-prefix=x86_64-w64-mingw32-`); x86 assembly needs `nasm`.
- [I] BtbN's automated builds are rolling: old assets are removed, so a pinned URL does not
  stay valid; there are no macOS builds there.
