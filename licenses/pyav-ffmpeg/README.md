# FFmpeg inside PyAV

PyAV's wheel carries its own FFmpeg libraries (`_internal/av.libs/`), which
the app uses to decode and compress replay video. They are a GPL build:
FFmpeg is configured with `--enable-version3` and links libx264 and libx265.

Version: 17.1.0

That is the PyAV version these texts describe; the build refuses another.
PyAV 17.1.0's `scripts/ffmpeg-latest.json` names pyav-ffmpeg release
`8.1.1-2` (FFmpeg 8.1.1). Its complete source is that release's recipe,
<https://github.com/PyAV-Org/pyav-ffmpeg/tree/8.1.1-2>, whose
`scripts/pkg.py` names the exact source archive of every library below and
`scripts/build-ffmpeg.py` the configuration.

| Folder | Library | License |
| --- | --- | --- |
| FFmpeg | FFmpeg 8.1.1 | GPL-3.0-or-later as built (LICENSE.md explains; all FFmpeg license texts) |
| x264 | x264 at b35605ac | GPL-2.0-or-later |
| x265 | x265 4.2 | GPL-2.0-or-later |
| lame | LAME 3.100 | LGPL-2.0-or-later (the GNU Library GPL v2 text LAME ships as COPYING) |
| opus | Opus 1.6.1 | BSD-3-Clause |
| dav1d | dav1d 1.5.3 | BSD-2-Clause |
| SVT-AV1 | SVT-AV1 4.1.0 | BSD-3-Clause-Clear, with the AOM patent license |
| libvpx | libvpx 1.16.0 | BSD-3-Clause, with WebM's patent grant |
| libwebp | libwebp 1.6.0 (with sharpyuv) | BSD-3-Clause, with WebM's patent grant |
| opencore-amr | opencore-amr 0.1.6 | Apache-2.0, with its NOTICE |
| libvpl | Intel libvpl 2.16.0 | MIT |
| nv-codec-headers | NVIDIA headers n13.0.19.0 | MIT (the header's own notice) |
| AMF | AMD AMF headers 1.5.0 | MIT |
| zlib | zlib | zlib |
| libiconv | GNU libiconv (MinGW) | LGPL-2.1-or-later |
| gcc-runtime | libgcc, libstdc++ (MinGW) | GPL-3.0 with the GCC Runtime Library Exception |
| winpthreads | MinGW-w64 winpthreads | MIT and BSD notices |

Texts were fetched on 2026-10-24 from each project's repository at the
version above (zlib, libiconv and the MinGW runtime at their current
release; their license terms do not vary by version).
