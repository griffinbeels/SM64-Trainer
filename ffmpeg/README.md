# ffmpeg.exe, built for the trainer

The app bundles one `ffmpeg.exe`, and it is this build: FFmpeg 8.1.1 made
by `tools/build_ffmpeg.py` from the inputs `build-inputs.json` pins. It
prints `ffmpeg version 8.1.1-sm64trainer`.

    uv run python tools/build_ffmpeg.py             # the cached build, built if missing; prints its path
    uv run python tools/build_ffmpeg.py --rebuild   # build again (the repeatability check)
    uv run python tools/build_ffmpeg.py --notices   # rewrite licenses/ffmpeg/BUILD-README.txt

`tools/build_exe.py` bundles it, and `tools/third_party_notices.py` refuses
any other binary: its SHA-256 must be `expected_sha256`.

## Why our own build

FFmpeg here is GPL v3 or later, so every release must offer the complete
source of the exact binary it ships, from the same place (GPL v3 section 6).
gyan.dev's full build, which the app shipped until 2026-11-08, links 82
external libraries. Its README points only to FFmpeg's own commit, not to
the sources of those libraries, and mirroring all of them would have been a
large job.

This build uses the sources PyAV's own FFmpeg is built from (pyav-ffmpeg
8.1.1-2; PyAV ships FFmpeg inside the app too). One source archive therefore
covers both: `SM64Trainer-third-party-source-ffmpeg.tar`, which
`tools/corresponding_source.py` writes and every release attaches.

## What is inside

The build is trimmed to what the app runs. `--disable-everything` turns
every component off, and `components` in `build-inputs.json` turns back on
only what the app's command lines name. These are:

- **the recorder's video sink**: NUT pictures and PCM in, then libx264,
  NVENC, AMF or Quick Sync; AAC audio; fragmented MP4 and segment output;
- **the startup encoder probe**: the same encoders, with proof that each
  picture comes back out;
- **replay compression**: AV1 and H.264 hardware encoders, `ssim`, and
  `framemd5`;
- **the failure compilation**: concat, scale, pad and crop;
- **comparison-video import and yt-dlp's merge**: decoders for what
  YouTube, Twitch and local files carry (H.264, HEVC, VP8, VP9, AV1 through
  dav1d, MPEG-4, AAC, Opus, Vorbis, MP3, FLAC, AC-3), and their demuxers.
  The network protocols are file, pipe, http, https, tls and tcp, with HLS
  through its demuxer.

TLS is Windows' own Schannel, so no TLS library is linked. The external
libraries are x264, dav1d, libvpl, NVIDIA's and AMD's headers, and zlib.
The MinGW-w64 runtime, winpthreads and the GCC runtime (libvpl is C++) are
linked statically. Each one's license text is in `licenses/ffmpeg/`.

`test_components` adds what the tests' own ffmpeg command lines build their
fixtures with: the lavfi input and its `color`, `testsrc`, `testsrc2`,
`sine` and `anullsrc` sources (their pictures arrive as `wrapped_avframe`,
so that decoder is included too), the `select` filter, and the `tee` muxer.
The app uses none of them. They are there so the suite tests the binary the
app ships. Without them, 23 of the focused replay tests failed and 49 were
skipped.

Unlike gyan.dev's build, this one carries no ffprobe or ffplay; the app
never needed them. FFmpeg 8.1 also removed the old `hls` protocol handler
(its Changelog: "Remove the old HLS protocol handler"). HLS still plays
through the `hls` demuxer.

`verify()` checks every pinned component against the binary's own listings.
It is needed because configure ignores a component name it does not know:
`png_pipe` must be spelled `image_png_pipe`, and `s16le` must be
`pcm_s16le`.

## How it is built

Each run follows the same steps:

- **The toolchain:** a portable MSYS2 base and the exact UCRT64 packages
  (`msys2.packages`, from repo.msys2.org by SHA-256) are unpacked under the
  build cache, `%LOCALAPPDATA%\SM64Trainer\build-cache\ffmpeg`. Nothing is
  installed on the machine, and no PATH or registry setting changes.
- **The sources:** each pinned archive is checked by its SHA-256 and
  unpacked. Then the patches in `patches/` are applied. Each patch edit must
  match exactly once.
- **The patch:** `libvpl-mingw.json` changes one guard in libvpl so that
  MinGW-w64 14's own `wcscpy_s` is not redefined.
- **The compiles:** zlib, the headers, x264, dav1d and libvpl are built
  static into a prefix, then FFmpeg is built. Every compiler runs hidden, at
  below-normal priority, with 12 parallel jobs.

Two builds give the same bytes. No path of this machine reaches the binary:
the configure line names none, and the libraries are built with
`-ffile-prefix-map`. The PE header carries no link time
(`--no-insert-timestamp`), and `SOURCE_DATE_EPOCH` is fixed. The cache is
keyed by a digest of the inputs, the patches and the build script.

## Changing it

To change a source, a package, a flag or a component:

1. Edit `build-inputs.json` and remove `expected_sha256`.
2. Build twice (`--rebuild`), and confirm that both builds have the same
   SHA-256.
3. Pin that SHA-256 as `expected_sha256`.
4. Run `--notices`.
5. Add any new source archive to `licenses/third-party-sources.json`.
   `tests/test_corresponding_source.py` refuses a build input that no
   release attaches.
