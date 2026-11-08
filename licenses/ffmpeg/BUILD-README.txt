ffmpeg.exe for SM64 Trainer

Version: 8.1.1-sm64trainer

SHA-256: e611814e87ee9c77141ca7587298212612d98762032ca882b11816963b03b61e

License: GPL version 3 or later. FFmpeg is configured with --enable-gpl
--enable-version3, and links x264 (GPL v2 or later).

Source: the source archives on the release page of the SM64 Trainer
version this copy came with: SM64Trainer-third-party-source-ffmpeg.tar, with its .sha256.
It holds every archive below. The recipe is tools/build_ffmpeg.py and
ffmpeg/build-inputs.json in the trainer's repository at that release's tag:
the sources PyAV's own FFmpeg comes from (pyav-ffmpeg 8.1.1-2), built
with a portable MSYS2 UCRT64 toolchain whose every package is pinned.

Sources:
  zlib: https://zlib.net/fossils/zlib-1.3.1.tar.gz
    sha256 9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23
  nv-codec-headers: https://github.com/FFmpeg/nv-codec-headers/archive/refs/tags/n13.0.19.0.tar.gz
    sha256 86d15d1a7c0ac73a0eafdfc57bebfeba7da8264595bf531cf4d8db1c22940116
  amf-headers: https://github.com/GPUOpen-LibrariesAndSDKs/AMF/releases/download/v1.5.0/AMF-headers-v1.5.0.tar.gz
    sha256 d569647fa26f289affe81a206259fa92f819d06db1e80cc334559953e82a3f01
  libvpl: https://github.com/intel/libvpl/archive/refs/tags/v2.16.0.tar.gz
    sha256 d60931937426130ddad9f1975c010543f0da99e67edb1c6070656b7947f633b6
  dav1d: https://code.videolan.org/videolan/dav1d/-/archive/1.5.3/dav1d-1.5.3.tar.bz2
    sha256 e099f53253f6c247580c554d53a13f1040638f2066edc3c740e4c2f15174ce22
  x264: https://code.videolan.org/videolan/x264/-/archive/b35605ace3ddf7c1a5d67a2eb553f034aef41d55/x264-b35605ace3ddf7c1a5d67a2eb553f034aef41d55.tar.bz2
    sha256 6eeb82934e69fd51e043bd8c5b0d152839638d1ce7aa4eea65a3fedcf83ff224
  ffmpeg: https://ffmpeg.org/releases/ffmpeg-8.1.1.tar.xz
    sha256 b6863adde98898f42602017462871b5f6333e65aec803fdd7a6308639c52edf3

The compiler's runtime, linked into the program (libgcc, libstdc++, winpthreads, the
MinGW-w64 C runtime), is a System Library under GPL v3 section 1 and carries the GCC
Runtime Library Exception or MIT- and BSD-style licenses, so its source is not attached
(third-party-sources.json, not_attached, quotes why). Its texts are in this folder.
Its source packages are:
  https://repo.msys2.org/mingw/sources/mingw-w64-gcc-16.2.0-6.src.tar.zst
    sha256 a901d8afb78438abcff9ed020cd9aa9431db76de1992171c2454c3c07ad289e8
  https://repo.msys2.org/mingw/sources/mingw-w64-winpthreads-14.0.0.r598.ge3d18511f-1.src.tar.zst
    sha256 1da3dad4f6916d0c23e8fe76c38ed79946fb32078f94047c0475229b8112a1db

Inside ffmpeg.exe, with the license texts in this folder:
  FFmpeg/: FFmpeg 8.1.1 (libavcodec, libavformat, libavfilter, libavutil, libswscale, libswresample and the ffmpeg program)
    GPL-3.0-or-later as built; LICENSE.md explains
  x264/: x264 at b35605ac
    GPL-2.0-or-later
  dav1d/: dav1d 1.5.3
    BSD-2-Clause
  libvpl/: Intel libvpl 2.16.0 (the dispatcher for Intel's encoders)
    MIT
  nv-codec-headers/: NVIDIA's nv-codec-headers n13.0.19.0
    MIT (the header's own notice)
  AMF/: AMD's AMF headers 1.5.0
    MIT (the headers' own notice)
  zlib/: zlib 1.3.1
    Zlib
  mingw-w64-runtime/: the MinGW-w64 C runtime start-up code
    the notices in its text
  winpthreads/: MinGW-w64 winpthreads
    MIT and BSD notices
  gcc-runtime/: libgcc and libstdc++ from GCC 16.2.0 (libvpl is C++)
    GPL-3.0 with the GCC Runtime Library Exception (COPYING.RUNTIME; the GPL v3 is FFmpeg/COPYING.GPLv3)

The same build also writes ffprobe.exe (--enable-ffprobe) for the trainer's tests;
the app does not ship it.

Configuration (what ffmpeg -version prints):
  --extra-version=sm64trainer
  --disable-everything
  --disable-autodetect
  --disable-doc
  --disable-debug
  --disable-programs
  --enable-ffmpeg
  --enable-gpl
  --enable-version3
  --enable-static
  --disable-shared
  --pkg-config=pkgconf
  --pkg-config-flags=--static
  --extra-ldflags=-static -Wl,--no-insert-timestamp
  --enable-w32threads
  --enable-zlib
  --enable-libx264
  --enable-libdav1d
  --enable-ffnvcodec
  --enable-cuda
  --enable-nvenc
  --enable-amf
  --enable-libvpl
  --enable-d3d11va
  --enable-dxva2
  --enable-schannel
  --enable-network
  --enable-ffprobe
  --enable-encoder=libx264,h264_nvenc,av1_nvenc,h264_amf,av1_amf,h264_qsv,av1_qsv,aac,pcm_s16le,wrapped_avframe,rawvideo,prores_ks,qtrle,png
  --enable-decoder=rawvideo,pcm_s16le,pcm_s16be,pcm_s24le,pcm_s32le,pcm_f32le,pcm_u8,h264,hevc,vp8,vp9,libdav1d,mpeg4,mpeg2video,mpeg1video,png,mjpeg,aac,aac_latm,opus,vorbis,mp3float,mp2float,flac,ac3,eac3,alac,wrapped_avframe,qtrle
  --enable-demuxer=nut,mov,matroska,mpegts,mpegps,rawvideo,pcm_s16le,wav,concat,image2,image_png_pipe,hls,flv,live_flv,mp3,ogg,aac,avi,ivf,h264,hevc,m4v,ffmetadata
  --enable-muxer=mp4,mov,ipod,framemd5,null,segment,mpegts,rawvideo,flv,matroska,webm,nut,ffmetadata,wav,tee
  --enable-filter=aresample,aformat,anull,abuffer,abuffersink,atrim,asetpts,apad,asplit,volume,buffer,buffersink,null,format,scale,pad,setsar,setdar,fps,concat,crop,vflip,hflip,transpose,rotate,trim,setpts,setparams,ssim,split,copy,color,testsrc,testsrc2,sine,anullsrc,select,lut,negate
  --enable-bsf=setts,aac_adtstoasc,vp9_superframe,vp9_superframe_split,h264_mp4toannexb,hevc_mp4toannexb,extract_extradata,null
  --enable-parser=h264,hevc,vp8,vp9,av1,mpeg4video,mpegvideo,aac,aac_latm,opus,vorbis,mpegaudio,flac,ac3,png,mjpeg
  --enable-protocol=file,pipe,concat,http,https,tls,tcp,crypto,data,httpproxy
  --enable-indev=lavfi
