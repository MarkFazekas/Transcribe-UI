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
  entry has nothing about it, so 6.10 is taken as right.
- [I] A `.png` icon is converted to `.ico`/`.icns` when Pillow is installed in the build
  environment.
- [V] The Qt hooks collect Qt's own translations (`PySide6/Qt/translations/qtbase_*.qm`) with
  QtCore (local Linux build, 6.22.3); `gui-bundle` checks it per build.
- [V] `pyi-archive_viewer` (`python -m PyInstaller.utils.cliutils.archive_viewer`; 6.22.3:
  `--list`, `--recursive`, `--brief`) lists a one-file executable: data and binaries as paths,
  then the PYZ's modules by dotted name (source of 6.22.3).
- [V] PyInstaller 6.22.3 has no command-line option to leave out a shared library its
  dependency analysis finds (only a `.spec` can filter `a.binaries`); its Linux exclude list
  (`PyInstaller/depend/dylib.py`) covers glibc, the GL/EGL/libdrm stack, `libxcb` and
  `libwayland-*`, but not `libstdc++` or `libgcc_s`, and `dylib.include_library` reads the
  module attribute `exclude_list` on every call (source of 6.22.3).
- [V] On Linux PyInstaller runs `ldd` (dependencies), `objdump` (ELF check) and `objcopy`
  (appends the archive to a one-file executable) (source of 6.22.3). A library it cannot
  resolve on the build machine is only a warning and stays out of the bundle.
- [V] `--add-data SRC:DEST` takes `:` on every OS in PyInstaller 6 (`os.pathsep` still
  accepted) (source of 6.22.3, `building/makespec.py`).
- [V] `types-pyinstaller` (typeshed) types PyInstaller's public API; `PyInstaller.depend.dylib`
  is not in it.
- [V] platformdirs 4.14 honours `WIN_PD_OVERRIDE_<CSIDL name>` (e.g.
  `WIN_PD_OVERRIDE_LOCAL_APPDATA`) on Windows and the XDG variables on macOS (except the log
  folder, under `~/Library/Logs`) (source of 4.14.8).
- [V] Measured on a local Linux build (WSL Ubuntu, uv's CPython 3.13.13, PyInstaller 6.22.3,
  PySide6 6.12.0, no ffmpeg yet): CLI 48.6 MB, GUI 102.1 MB, CLI cold start (`--version`)
  1.4 s, the GUI's offscreen smoke run 3.4 s. python-build-standalone's Linux CPython ships
  `libpython3.13.so.1.0`, which PyInstaller bundles; the manylinux image's `/opt/python`
  builds have no shared library and fail ("Python was built without a shared library"), and a
  runner's Homebrew or tool-cache Python is found first unless uv is limited to its own
  (`UV_PYTHON_PREFERENCE=only-managed`; first CI run of PR #53). The PySide6 6.12 Linux hooks collect the platform plugins `qoffscreen`, `qxcb`,
  `qwayland` (plus `eglfs`, `linuxfb`, `minimal`, `vnc`, `vkkhrdisplay`) and
  `PySide6/Qt/translations/qtbase_hu.qm`. The Qt libraries link libglib, libfreetype,
  libxkbcommon, libX11, the xcb utility libraries, libwayland and GL/EGL from the system.
- [I] Modules imported dynamically (`importlib.import_module`) are invisible to the analysis and
  need `--hidden-import` or `--collect-submodules`; data read through `importlib.resources` must
  be collected as data (`.py` files included where a tool reads them as files, as Alembic does
  with its revisions).

## PySide6 wheels

- [V] Qt's macOS wheels carry a higher Mach-O minimum macOS than their platform tag:
  shiboken6 6.12.0 (tag `macosx_14_0`) has `LC_BUILD_VERSION minos 26.0` (sdk 26.0) in
  `libshiboken6.abi3.6.12.dylib` and `Shiboken.abi3.so`, libpyside6 the same; the Qt frameworks
  and plugins of pyside6-essentials 6.12 have `minos 14.4`; shiboken6 6.11.2 (tag
  `macosx_13_0`) has `minos 15.0`. Checked with `llvm-objdump` on the PyPI wheels and by the
  macOS legs of PR #53's run 46150560721. So the `minos` of a wheel's own
  dylibs is not a floor signal; the build checks wheel binaries by their tag
  (`scripts/build_desktop_macos.py`).
- [V] dyld loads a dylib whose `minos` is newer than the running macOS: on the `macos-15` images
  (arm64, image `macos-15-arm64` 20261123, and x64) the executables with shiboken6 and
  PySide6_Essentials binaries of `minos 26.0` pass `gui-offscreen` and `gui-native` (the GUI
  executable; the CLI bundles no Qt) (PR #53, run 46153311250; every leg built with uv's
  CPython 3.13.18). Only a
  main executable's `minos` is enforced at start [I].
- [I] Whether those binaries' symbols newer than macOS 14.0 resolve on macOS 14 is unproven: no
  hosted macOS 14 runner exists; the manual acceptance (A24) on a macOS 14 machine would show it.

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
- [V] `macos-14` (and `-large`, `-xlarge`) was retired on 2026-11-02 (brownouts in October
  2026); the recommended labels are `macos-15`, `macos-26` and their `-xlarge` arm64 variants
  (https://github.blog/changelog/2026-10-01-github-actions-macos-14-runner-image-retirement). No
  hosted macOS 14 runner remains, and there was never an Intel `macos-14` label for free plans
  [I]; a macOS 14 start can only be checked on a machine of one's own.
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
- [V] Pinned in `scripts/build_ffmpeg_pins.py` (2026-11-28): `ffmpeg-9.1.tar.xz`, SHA-256
  `1a4d394ae63d190a526cd052eb4415c156b5c5e2df37efab41a7de0b8fde2433`, its `.asc` a good
  signature by the FFmpeg release signing key `FCF9 86EA 15E6 E293 A564 4F10 B432 2F04 D676
  58D8` (key from https://ffmpeg.org/ffmpeg-devel.asc); `zlib-1.4.tar.gz` (1507140 bytes),
  SHA-256 `02dd159783677a749c04adbdd02b55b8a2bcc7e054f51b76b549199a916ad584` (as zlib.net lists
  it), its `.asc` a good signature by Mark Adler's key `5ED4 6A67 21D3 6558 7791 E2AA 783F CD8E
  58BC AFBA` (from keys.openpgp.org). The same bytes come from the GitHub release asset
  `https://github.com/madler/zlib/releases/download/v1.4/zlib-1.4.tar.gz`, from
  `https://zlib.net/fossils/` and from `https://zlib.net/` (checked from a home connection).
  zlib 1.4 (2026-11) still ships `./configure` (`--static`; `CHOST=x86_64-w64-mingw32` for the
  mingw-w64 cross build) next to CMake.
- [V] zlib.net serves GitHub-hosted runners something other than the tarball: in run
  46158956325 (PR #56, 2026-11-28) `https://zlib.net/fossils/zlib-1.4.tar.gz` hashed
  `2d16dd04…` on one runner and `00b7f82e…` on another, a different body each time (most
  likely a bot-protection page; the run did not record its content). So the GitHub release
  asset is the primary URL and zlib.net the fallback; `build_ffmpeg_steps.download` logs
  curl's HTTP status, content type and size, and a wrong download's size and first bytes.
- [V] FFmpeg's release tarballs exist only on ffmpeg.org (`/releases/`): the GitHub mirror
  `FFmpeg/FFmpeg` publishes no release assets, and a tag's generated archive is other bytes
  than the release tarball (no PGP signature, and the release's generated files differ), so
  FFmpeg has no fallback URL [I: the tag archive's content was not compared].
- [V] FFmpeg 9.1's `configure`: `--disable-autodetect` turns off the libraries of
  `EXTERNAL_AUTODETECT_LIBRARY_LIST` (zlib, bzlib, lzma, iconv, SDL2, xlib, libxcb, the Apple
  frameworks, schannel, securetransport, ...) and `HWACCEL_AUTODETECT_LIBRARY_LIST`, but not
  `THREADS_LIST` (a "TODO" comment there): the thread libraries stay autodetected. It also
  enables `libc_iconv`, so the C library's iconv is still used unless `--disable-iconv` is
  given. A library given with `--enable-<lib>` (zlib, pthreads, w32threads) is "requested":
  configure dies when it is not found, instead of leaving it out. The `ffmpeg` program
  depends on `threads` (`ffmpeg_deps="avcodec avfilter avformat threads"`), so without a
  thread library configure drops it silently; `ffprobe` needs none.
- [V] FFmpeg 9.1's Matroska demuxer decompresses zlib-compressed tracks only when built with
  zlib (`#if CONFIG_ZLIB` in `libavformat/matroskadec.c`); without it the encoding is
  "Unsupported encoding type" and the compressed bytes pass through as the payload, so text
  cues come out garbled. mkvmerge 82.0 writes such a track with `--compression 0:zlib`
  (`mkvinfo` shows "Content compression"; zlib is the default algorithm, so no algorithm line).
- [V] `ffmpeg -buildconf` prints the configure line one argument per line on stderr after a
  `configuration:` heading, followed by `Exiting with exit code 0`; an argument after one that
  ends in `pkg-config` stays on the same line (`fftools/opt_common.c` keeps
  `--pkg-config=pkg-config --extra-ldflags=-static` together). Configure records each argument
  with the part after its first `=` shell-quoted when it holds a character other than letters,
  digits and `_/.+-` (`sh_quote` in `configure`), so the macOS legs print
  `--extra-cflags='-mmacosx-version-min=14.0'` (seen in run 46160789188 of PR #56);
  `build_ffmpeg_checks.buildconf_arguments` reads each line with `shlex`, which removes the
  quotes and splits the joined `pkg-config` line. `ffmpeg -L` prints the licence on
  stdout; without `--enable-gpl`, `--enable-nonfree` and `--enable-version3` it is "GNU Lesser
  General Public License ... either version 2.1 of the License, or (at your option) any later
  version" (same file).
- [V] FFmpeg 9.1's native Opus encoder (`-c:a opus`) refuses to run without `-strict
  experimental` ("Experimental feature"); it takes 48 kHz only, and the ffmpeg program
  resamples other rates to it by itself (`-ar 48000` makes it explicit). The probed codec name
  is `opus`, as with libopus. `libopus` is an external library the bundled build lacks.
- [V] Measured local builds of `scripts/build_ffmpeg.py` (WSL Ubuntu 24.04, 12 threads,
  2026-11-28): Linux x64 (host gcc 13, nasm 2.16) about 3.5 min for FFmpeg plus 1 s for zlib,
  `ffmpeg` 29.9 MB and `ffprobe` 29.7 MB (stripped), `ldd`: `linux-vdso`, `libm`, `libc`, the
  loader; Windows x64 cross build (Ubuntu's mingw-w64 13.2 `-win32` variant, nasm 2.16) about
  4.6 min plus 2 s, `ffmpeg.exe` 29.3 MB and `ffprobe.exe` 29.1 MB, importing only
  `bcrypt.dll`, `KERNEL32.dll`, `msvcrt.dll` and `SHELL32.dll`. Run on Windows 11 (build
  26200, through WSL interop), the Windows programs pass `verify` and extract the cues of the
  zlib-compressed fixture. CI runners have 3 to 4 cores, so a cache miss costs roughly 10 to 15
  minutes per platform [I]. The macOS builds were not run locally (no Mac); their CI legs prove
  them.
- [V] AlmaLinux 9 (the manylinux_2_34 image) has `nasm` 2.15 in its CRB repository and
  `pkgconf-pkg-config` in BaseOS (https://repo.almalinux.org/almalinux/9/).
- [I] Windows builds are commonly cross-compiled on Linux with mingw-w64
  (`--target-os=mingw32 --cross-prefix=x86_64-w64-mingw32-`); x86 assembly needs `nasm`.
- [V] With `--extra-ldflags=-static` and `--enable-w32threads` the mingw-w64 build needs no
  mingw runtime DLL (see the measured import list above). [I] Without `-static` a mingw-w64
  build links the GCC runtime dynamically (`libgcc_s_seh-1.dll`, and `libwinpthread-1.dll`
  with POSIX threads).
- [I] macOS ships `libz` in `/usr/lib` (in the dyld shared cache) on every version; there is
  no static system copy, so a macOS build links the system's.
- [I] BtbN's automated builds are rolling: old assets are removed, so a pinned URL does not
  stay valid; there are no macOS builds there.
