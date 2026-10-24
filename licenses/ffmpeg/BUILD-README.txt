FFmpeg 64-bit static Windows build from www.gyan.dev

Version: 2025-10-09-git-469aad3897-full_build-www.gyan.dev

License: GPL v3

Source Code: https://github.com/FFmpeg/FFmpeg/commit/469aad3897

External Assets
frei0r plugins:   https://www.gyan.dev/ffmpeg/builds/ffmpeg-frei0r-plugins
lensfun database: https://www.gyan.dev/ffmpeg/builds/ffmpeg-lensfun-db
whisper models:   https://huggingface.co/ggerganov/whisper.cpp/tree/main

git-full build configuration: 

ARCH                      x86 (generic)
big-endian                no
runtime cpu detection     yes
standalone assembly       yes
x86 assembler             nasm
MMX enabled               yes
MMXEXT enabled            yes
3DNow! enabled            yes
3DNow! extended enabled   yes
SSE enabled               yes
SSSE3 enabled             yes
AESNI enabled             yes
AVX enabled               yes
AVX2 enabled              yes
AVX-512 enabled           yes
AVX-512ICL enabled        yes
XOP enabled               yes
FMA3 enabled              yes
FMA4 enabled              yes
i686 features enabled     yes
CMOV is fast              yes
EBX available             yes
EBP available             yes
debug symbols             yes
strip symbols             yes
optimize for size         no
optimizations             yes
static                    yes
shared                    no
network support           yes
threading support         pthreads
safe bitstream reader     yes
texi2html enabled         no
perl enabled              yes
pod2man enabled           yes
makeinfo enabled          yes
makeinfo supports HTML    yes
experimental features     yes
xmllint enabled           yes

External libraries:
avisynth                libharfbuzz             libtheora
bzlib                   libilbc                 libtwolame
chromaprint             libjxl                  libuavs3d
frei0r                  liblc3                  libvidstab
gmp                     liblensfun              libvmaf
gnutls                  libmodplug              libvo_amrwbenc
iconv                   libmp3lame              libvorbis
ladspa                  libmysofa               libvpx
lcms2                   liboapv                 libvvenc
libaom                  libopencore_amrnb       libwebp
libaribb24              libopencore_amrwb       libx264
libaribcaption          libopenjpeg             libx265
libass                  libopenmpt              libxavs2
libbluray               libopus                 libxevd
libbs2b                 libplacebo              libxeve
libcaca                 libqrencode             libxml2
libcdio                 libquirc                libxvid
libcodec2               librav1e                libzimg
libdav1d                librist                 libzmq
libdavs2                librubberband           libzvbi
libdvdnav               libshaderc              lzma
libdvdread              libshine                mediafoundation
libflite                libsnappy               openal
libfontconfig           libsoxr                 sdl2
libfreetype             libspeex                whisper
libfribidi              libsrt                  zlib
libgme                  libssh
libgsm                  libsvtav1

External libraries providing hardware acceleration:
amf                     d3d12va                 nvdec
cuda                    dxva2                   nvenc
cuda_llvm               ffnvcodec               opencl
cuvid                   libmfx                  vaapi
d3d11va                 libvpl                  vulkan

Libraries:
avcodec                 avformat                swscale
avdevice                avutil
avfilter                swresample

Programs:
ffmpeg                  ffplay                  ffprobe

Enabled decoders:
aac                     fmvc                    pcm_u32le
aac_fixed               fourxm                  pcm_u8
aac_latm                fraps                   pcm_vidc
aasc                    frwu                    pcx
ac3                     ftr                     pdv
ac3_fixed               g2m                     pfm
acelp_kelvin            g723_1                  pgm
adpcm_4xm               g728                    pgmyuv
adpcm_adx               g729                    pgssub
adpcm_afc               gdv                     pgx
adpcm_agm               gem                     phm
adpcm_aica              gif                     photocd
adpcm_argo              gremlin_dpcm            pictor
adpcm_circus            gsm                     pixlet
adpcm_ct                gsm_ms                  pjs
adpcm_dtk               h261                    png
adpcm_ea                h263                    ppm
adpcm_ea_maxis_xa       h263i                   prores
adpcm_ea_r1             h263p                   prores_raw
adpcm_ea_r2             h264                    prosumer
adpcm_ea_r3             h264_amf                psd
adpcm_ea_xas            h264_cuvid              ptx
adpcm_g722              h264_qsv                qcelp
adpcm_g726              hap                     qdm2
adpcm_g726le            hca                     qdmc
adpcm_ima_acorn         hcom                    qdraw
adpcm_ima_alp           hdr                     qoa
adpcm_ima_amv           hevc                    qoi
adpcm_ima_apc           hevc_amf                qpeg
adpcm_ima_apm           hevc_cuvid              qtrle
adpcm_ima_cunning       hevc_qsv                r10k
adpcm_ima_dat4          hnm4_video              r210
adpcm_ima_dk3           hq_hqa                  ra_144
adpcm_ima_dk4           hqx                     ra_288
adpcm_ima_ea_eacs       huffyuv                 ralf
adpcm_ima_ea_sead       hymt                    rasc
adpcm_ima_escape        iac                     rawvideo
adpcm_ima_hvqm2         idcin                   realtext
adpcm_ima_hvqm4         idf                     rka
adpcm_ima_iss           iff_ilbm                rl2
adpcm_ima_magix         ilbc                    roq
adpcm_ima_moflex        imc                     roq_dpcm
adpcm_ima_mtf           imm4                    rpza
adpcm_ima_oki           imm5                    rscc
adpcm_ima_pda           indeo2                  rtv1
adpcm_ima_qt            indeo3                  rv10
adpcm_ima_rad           indeo4                  rv20
adpcm_ima_smjpeg        indeo5                  rv30
adpcm_ima_ssi           interplay_acm           rv40
adpcm_ima_wav           interplay_dpcm          rv60
adpcm_ima_ws            interplay_video         s302m
adpcm_ima_xbox          ipu                     sami
adpcm_ms                jacosub                 sanm
adpcm_mtaf              jpeg2000                sbc
adpcm_n64               jpegls                  scpr
adpcm_psx               jv                      screenpresso
adpcm_psxc              kgv1                    sdx2_dpcm
adpcm_sanyo             kmvc                    sga
adpcm_sbpro_2           lagarith                sgi
adpcm_sbpro_3           lead                    sgirle
adpcm_sbpro_4           libaom_av1              sheervideo
adpcm_swf               libaribb24              shorten
adpcm_thp               libaribcaption          simbiosis_imx
adpcm_thp_le            libcodec2               sipr
adpcm_vima              libdav1d                siren
adpcm_xa                libdavs2                smackaud
adpcm_xmd               libgsm                  smacker
adpcm_yamaha            libgsm_ms               smc
adpcm_zork              libilbc                 smvjpeg
agm                     libjxl                  snow
ahx                     libjxl_anim             sol_dpcm
aic                     liblc3                  sonic
alac                    libopencore_amrnb       sp5x
alias_pix               libopencore_amrwb       speedhq
als                     libopus                 speex
amrnb                   libspeex                srgc
amrwb                   libuavs3d               srt
amv                     libvorbis               ssa
anm                     libvpx_vp8              stl
ansi                    libvpx_vp9              subrip
anull                   libxevd                 subviewer
apac                    libzvbi_teletext        subviewer1
ape                     loco                    sunrast
apng                    lscr                    svq1
aptx                    m101                    svq3
aptx_hd                 mace3                   tak
apv                     mace6                   targa
arbc                    magicyuv                targa_y216
argo                    mdec                    tdsc
ass                     media100                text
asv1                    metasound               theora
asv2                    microdvd                thp
atrac1                  mimic                   tiertexseqvideo
atrac3                  misc4                   tiff
atrac3al                mjpeg                   tmv
atrac3p                 mjpeg_cuvid             truehd
atrac3pal               mjpeg_qsv               truemotion1
atrac9                  mjpegb                  truemotion2
aura                    mlp                     truemotion2rt
aura2                   mmvideo                 truespeech
av1                     mobiclip                tscc
av1_amf                 motionpixels            tscc2
av1_cuvid               movtext                 tta
av1_qsv                 mp1                     twinvq
avrn                    mp1float                txd
avrp                    mp2                     ulti
avs                     mp2float                utvideo
avui                    mp3                     v210
bethsoftvid             mp3adu                  v210x
bfi                     mp3adufloat             v308
bink                    mp3float                v408
binkaudio_dct           mp3on4                  v410
binkaudio_rdft          mp3on4float             vb
bintext                 mpc7                    vble
bitpacked               mpc8                    vbn
bmp                     mpeg1_cuvid             vc1
bmv_audio               mpeg1video              vc1_cuvid
bmv_video               mpeg2_cuvid             vc1_qsv
bonk                    mpeg2_qsv               vc1image
brender_pix             mpeg2video              vcr1
c93                     mpeg4                   vmdaudio
cavs                    mpeg4_cuvid             vmdvideo
cbd2_dpcm               mpegvideo               vmix
ccaption                mpl2                    vmnc
cdgraphics              msa1                    vnull
cdtoons                 mscc                    vorbis
cdxl                    msmpeg4v1               vp3
cfhd                    msmpeg4v2               vp4
cinepak                 msmpeg4v3               vp5
clearvideo              msnsiren                vp6
cljr                    msp2                    vp6a
cllc                    msrle                   vp6f
comfortnoise            mss1                    vp7
cook                    mss2                    vp8
cpia                    msvideo1                vp8_cuvid
cri                     mszh                    vp8_qsv
cscd                    mts2                    vp9
cyuv                    mv30                    vp9_amf
dca                     mvc1                    vp9_cuvid
dds                     mvc2                    vp9_qsv
derf_dpcm               mvdv                    vplayer
dfa                     mvha                    vqa
dfpwm                   mwsc                    vqc
dirac                   mxpeg                   vvc
dnxhd                   nellymoser              vvc_qsv
dolby_e                 notchlc                 wady_dpcm
dpx                     nuv                     wavarc
dsd_lsbf                on2avc                  wavpack
dsd_lsbf_planar         opus                    wbmp
dsd_msbf                osq                     wcmv
dsd_msbf_planar         paf_audio               webp
dsicinaudio             paf_video               webvtt
dsicinvideo             pam                     wmalossless
dss_sp                  pbm                     wmapro
dst                     pcm_alaw                wmav1
dvaudio                 pcm_bluray              wmav2
dvbsub                  pcm_dvd                 wmavoice
dvdsub                  pcm_f16le               wmv1
dvvideo                 pcm_f24le               wmv2
dxa                     pcm_f32be               wmv3
dxtory                  pcm_f32le               wmv3image
dxv                     pcm_f64be               wnv1
eac3                    pcm_f64le               wrapped_avframe
eacmv                   pcm_lxf                 ws_snd1
eamad                   pcm_mulaw               xan_dpcm
eatgq                   pcm_s16be               xan_wc3
eatgv                   pcm_s16be_planar        xan_wc4
eatqi                   pcm_s16le               xbin
eightbps                pcm_s16le_planar        xbm
eightsvx_exp            pcm_s24be               xface
eightsvx_fib            pcm_s24daud             xl
escape124               pcm_s24le               xma1
escape130               pcm_s24le_planar        xma2
evrc                    pcm_s32be               xpm
exr                     pcm_s32le               xsub
fastaudio               pcm_s32le_planar        xwd
ffv1                    pcm_s64be               y41p
ffvhuff                 pcm_s64le               ylc
ffwavesynth             pcm_s8                  yop
fic                     pcm_s8_planar           yuv4
fits                    pcm_sga                 zero12v
flac                    pcm_u16be               zerocodec
flashsv                 pcm_u16le               zlib
flashsv2                pcm_u24be               zmbv
flic                    pcm_u24le
flv                     pcm_u32be

Enabled encoders:
a64multi                hevc_nvenc              pcm_s32le_planar
a64multi5               hevc_qsv                pcm_s64be
aac                     hevc_vaapi              pcm_s64le
aac_mf                  hevc_vulkan             pcm_s8
ac3                     huffyuv                 pcm_s8_planar
ac3_fixed               jpeg2000                pcm_u16be
ac3_mf                  jpegls                  pcm_u16le
adpcm_adx               libaom_av1              pcm_u24be
adpcm_argo              libcodec2               pcm_u24le
adpcm_g722              libgsm                  pcm_u32be
adpcm_g726              libgsm_ms               pcm_u32le
adpcm_g726le            libilbc                 pcm_u8
adpcm_ima_alp           libjxl                  pcm_vidc
adpcm_ima_amv           libjxl_anim             pcx
adpcm_ima_apm           liblc3                  pfm
adpcm_ima_qt            libmp3lame              pgm
adpcm_ima_ssi           liboapv                 pgmyuv
adpcm_ima_wav           libopencore_amrnb       phm
adpcm_ima_ws            libopenjpeg             png
adpcm_ms                libopus                 ppm
adpcm_swf               librav1e                prores
adpcm_yamaha            libshine                prores_aw
alac                    libspeex                prores_ks
alias_pix               libsvtav1               qoi
amv                     libtheora               qtrle
anull                   libtwolame              r10k
apng                    libvo_amrwbenc          r210
aptx                    libvorbis               ra_144
aptx_hd                 libvpx_vp8              rawvideo
ass                     libvpx_vp9              roq
asv1                    libvvenc                roq_dpcm
asv2                    libwebp                 rpza
av1_amf                 libwebp_anim            rv10
av1_mf                  libx264                 rv20
av1_nvenc               libx264rgb              s302m
av1_qsv                 libx265                 sbc
av1_vaapi               libxavs2                sgi
av1_vulkan              libxeve                 smc
avrp                    libxvid                 snow
avui                    ljpeg                   speedhq
bitpacked               magicyuv                srt
bmp                     mjpeg                   ssa
cfhd                    mjpeg_qsv               subrip
cinepak                 mjpeg_vaapi             sunrast
cljr                    mlp                     svq1
comfortnoise            movtext                 targa
dca                     mp2                     text
dfpwm                   mp2fixed                tiff
dnxhd                   mp3_mf                  truehd
dpx                     mpeg1video              tta
dvbsub                  mpeg2_qsv               ttml
dvdsub                  mpeg2_vaapi             utvideo
dvvideo                 mpeg2video              v210
dxv                     mpeg4                   v308
eac3                    msmpeg4v2               v408
exr                     msmpeg4v3               v410
ffv1                    msrle                   vbn
ffv1_vulkan             msvideo1                vc2
ffvhuff                 nellymoser              vnull
fits                    opus                    vorbis
flac                    pam                     vp8_vaapi
flashsv                 pbm                     vp9_qsv
flashsv2                pcm_alaw                vp9_vaapi
flv                     pcm_bluray              wavpack
g723_1                  pcm_dvd                 wbmp
gif                     pcm_f32be               webvtt
h261                    pcm_f32le               wmav1
h263                    pcm_f64be               wmav2
h263p                   pcm_f64le               wmv1
h264_amf                pcm_mulaw               wmv2
h264_mf                 pcm_s16be               wrapped_avframe
h264_nvenc              pcm_s16be_planar        xbm
h264_qsv                pcm_s16le               xface
h264_vaapi              pcm_s16le_planar        xsub
h264_vulkan             pcm_s24be               xwd
hap                     pcm_s24daud             y41p
hdr                     pcm_s24le               yuv4
hevc_amf                pcm_s24le_planar        zlib
hevc_d3d12va            pcm_s32be               zmbv
hevc_mf                 pcm_s32le

Enabled hwaccels:
av1_d3d11va             hevc_dxva2              vc1_dxva2
av1_d3d11va2            hevc_nvdec              vc1_nvdec
av1_d3d12va             hevc_vaapi              vc1_vaapi
av1_dxva2               hevc_vulkan             vp8_nvdec
av1_nvdec               mjpeg_nvdec             vp8_vaapi
av1_vaapi               mjpeg_vaapi             vp9_d3d11va
av1_vulkan              mpeg1_nvdec             vp9_d3d11va2
ffv1_vulkan             mpeg2_d3d11va           vp9_d3d12va
h263_vaapi              mpeg2_d3d11va2          vp9_dxva2
h264_d3d11va            mpeg2_d3d12va           vp9_nvdec
h264_d3d11va2           mpeg2_dxva2             vp9_vaapi
h264_d3d12va            mpeg2_nvdec             vp9_vulkan
h264_dxva2              mpeg2_vaapi             vvc_vaapi
h264_nvdec              mpeg4_nvdec             wmv3_d3d11va
h264_vaapi              mpeg4_vaapi             wmv3_d3d11va2
h264_vulkan             prores_raw_vulkan       wmv3_d3d12va
hevc_d3d11va            vc1_d3d11va             wmv3_dxva2
hevc_d3d11va2           vc1_d3d11va2            wmv3_nvdec
hevc_d3d12va            vc1_d3d12va             wmv3_vaapi

Enabled parsers:
aac                     dvd_nav                 mpegaudio
aac_latm                dvdsub                  mpegvideo
ac3                     evc                     opus
adx                     ffv1                    png
ahx                     flac                    pnm
amr                     ftr                     prores_raw
apv                     g723_1                  qoi
av1                     g729                    rv34
avs2                    gif                     sbc
avs3                    gsm                     sipr
bmp                     h261                    tak
cavsvideo               h263                    vc1
cook                    h264                    vorbis
cri                     hdr                     vp3
dca                     hevc                    vp8
dirac                   ipu                     vp9
dnxhd                   jpeg2000                vvc
dnxuc                   jpegxl                  webp
dolby_e                 misc4                   xbm
dpx                     mjpeg                   xma
dvaudio                 mlp                     xwd
dvbsub                  mpeg4video

Enabled demuxers:
aa                      ico                     pcm_f64le
aac                     idcin                   pcm_mulaw
aax                     idf                     pcm_s16be
ac3                     iff                     pcm_s16le
ac4                     ifv                     pcm_s24be
ace                     ilbc                    pcm_s24le
acm                     image2                  pcm_s32be
act                     image2_alias_pix        pcm_s32le
adf                     image2_brender_pix      pcm_s8
adp                     image2pipe              pcm_u16be
ads                     image_bmp_pipe          pcm_u16le
adx                     image_cri_pipe          pcm_u24be
aea                     image_dds_pipe          pcm_u24le
afc                     image_dpx_pipe          pcm_u32be
aiff                    image_exr_pipe          pcm_u32le
aix                     image_gem_pipe          pcm_u8
alp                     image_gif_pipe          pcm_vidc
amr                     image_hdr_pipe          pdv
amrnb                   image_j2k_pipe          pjs
amrwb                   image_jpeg_pipe         pmp
anm                     image_jpegls_pipe       pp_bnk
apac                    image_jpegxl_pipe       pva
apc                     image_pam_pipe          pvf
ape                     image_pbm_pipe          qcp
apm                     image_pcx_pipe          qoa
apng                    image_pfm_pipe          r3d
aptx                    image_pgm_pipe          rawvideo
aptx_hd                 image_pgmyuv_pipe       rcwt
apv                     image_pgx_pipe          realtext
aqtitle                 image_phm_pipe          redspark
argo_asf                image_photocd_pipe      rka
argo_brp                image_pictor_pipe       rl2
argo_cvg                image_png_pipe          rm
asf                     image_ppm_pipe          roq
asf_o                   image_psd_pipe          rpl
ass                     image_qdraw_pipe        rsd
ast                     image_qoi_pipe          rso
au                      image_sgi_pipe          rtp
av1                     image_sunrast_pipe      rtsp
avi                     image_svg_pipe          s337m
avisynth                image_tiff_pipe         sami
avr                     image_vbn_pipe          sap
avs                     image_webp_pipe         sbc
avs2                    image_xbm_pipe          sbg
avs3                    image_xpm_pipe          scc
bethsoftvid             image_xwd_pipe          scd
bfi                     imf                     sdns
bfstm                   ingenient               sdp
bink                    ipmovie                 sdr2
binka                   ipu                     sds
bintext                 ircam                   sdx
bit                     iss                     segafilm
bitpacked               iv8                     ser
bmv                     ivf                     sga
boa                     ivr                     shorten
bonk                    jacosub                 siff
brstm                   jpegxl_anim             simbiosis_imx
c93                     jv                      sln
caf                     kux                     smacker
cavsvideo               kvag                    smjpeg
cdg                     laf                     smush
cdxl                    lc3                     sol
cine                    libgme                  sox
codec2                  libmodplug              spdif
codec2raw               libopenmpt              srt
concat                  live_flv                stl
dash                    lmlm4                   str
data                    loas                    subviewer
daud                    lrc                     subviewer1
dcstr                   luodat                  sup
derf                    lvf                     svag
dfa                     lxf                     svs
dfpwm                   m4v                     swf
dhav                    matroska                tak
dirac                   mca                     tedcaptions
dnxhd                   mcc                     thp
dsf                     mgsts                   threedostr
dsicin                  microdvd                tiertexseq
dss                     mjpeg                   tmv
dts                     mjpeg_2000              truehd
dtshd                   mlp                     tta
dv                      mlv                     tty
dvbsub                  mm                      txd
dvbtxt                  mmf                     ty
dvdvideo                mods                    usm
dxa                     moflex                  v210
ea                      mov                     v210x
ea_cdata                mp3                     vag
eac3                    mpc                     vc1
epaf                    mpc8                    vc1t
evc                     mpegps                  vividas
ffmetadata              mpegts                  vivo
filmstrip               mpegtsraw               vmd
fits                    mpegvideo               vobsub
flac                    mpjpeg                  voc
flic                    mpl2                    vpk
flv                     mpsub                   vplayer
fourxm                  msf                     vqf
frm                     msnwc_tcp               vvc
fsb                     msp                     w64
fwse                    mtaf                    wady
g722                    mtv                     wav
g723_1                  musx                    wavarc
g726                    mv                      wc3
g726le                  mvi                     webm_dash_manifest
g728                    mxf                     webvtt
g729                    mxg                     wsaud
gdv                     nc                      wsd
genh                    nistsphere              wsvqa
gif                     nsp                     wtv
gsm                     nsv                     wv
gxf                     nut                     wve
h261                    nuv                     xa
h263                    obu                     xbin
h264                    ogg                     xmd
hca                     oma                     xmv
hcom                    osq                     xvag
hevc                    paf                     xwma
hls                     pcm_alaw                yop
hnm                     pcm_f32be               yuv4mpegpipe
hxvs                    pcm_f32le
iamf                    pcm_f64be

Enabled muxers:
a64                     h261                    pcm_s16be
ac3                     h263                    pcm_s16le
ac4                     h264                    pcm_s24be
adts                    hash                    pcm_s24le
adx                     hds                     pcm_s32be
aea                     hevc                    pcm_s32le
aiff                    hls                     pcm_s8
alp                     iamf                    pcm_u16be
amr                     ico                     pcm_u16le
amv                     ilbc                    pcm_u24be
apm                     image2                  pcm_u24le
apng                    image2pipe              pcm_u32be
aptx                    ipod                    pcm_u32le
aptx_hd                 ircam                   pcm_u8
apv                     ismv                    pcm_vidc
argo_asf                ivf                     psp
argo_cvg                jacosub                 rawvideo
asf                     kvag                    rcwt
asf_stream              latm                    rm
ass                     lc3                     roq
ast                     lrc                     rso
au                      m4v                     rtp
avi                     matroska                rtp_mpegts
avif                    matroska_audio          rtsp
avm2                    mcc                     sap
avs2                    md5                     sbc
avs3                    microdvd                scc
bit                     mjpeg                   segafilm
caf                     mkvtimestamp_v2         segment
cavsvideo               mlp                     smjpeg
chromaprint             mmf                     smoothstreaming
codec2                  mov                     sox
codec2raw               mp2                     spdif
crc                     mp3                     spx
dash                    mp4                     srt
data                    mpeg1system             stream_segment
daud                    mpeg1vcd                streamhash
dfpwm                   mpeg1video              sup
dirac                   mpeg2dvd                swf
dnxhd                   mpeg2svcd               tee
dts                     mpeg2video              tg2
dv                      mpeg2vob                tgp
eac3                    mpegts                  truehd
evc                     mpjpeg                  tta
f4v                     mxf                     ttml
ffmetadata              mxf_d10                 uncodedframecrc
fifo                    mxf_opatom              vc1
filmstrip               null                    vc1t
fits                    nut                     voc
flac                    obu                     vvc
flv                     oga                     w64
framecrc                ogg                     wav
framehash               ogv                     webm
framemd5                oma                     webm_chunk
g722                    opus                    webm_dash_manifest
g723_1                  pcm_alaw                webp
g726                    pcm_f32be               webvtt
g726le                  pcm_f32le               wsaud
gif                     pcm_f64be               wtv
gsm                     pcm_f64le               wv
gxf                     pcm_mulaw               yuv4mpegpipe

Enabled protocols:
async                   http                    rtmp
bluray                  httpproxy               rtmpe
cache                   https                   rtmps
concat                  icecast                 rtmpt
concatf                 ipfs_gateway            rtmpte
crypto                  ipns_gateway            rtmpts
data                    librist                 rtp
fd                      libsrt                  srtp
ffrtmpcrypt             libssh                  subfile
ffrtmphttp              libzmq                  tcp
file                    md5                     tee
ftp                     mmsh                    tls
gopher                  mmst                    udp
gophers                 pipe                    udplite
hls                     prompeg

Enabled filters:
a3dscope                deblock                 perlin
aap                     decimate                perms
abench                  deconvolve              perspective
abitscope               dedot                   phase
acompressor             deesser                 photosensitivity
acontrast               deflate                 pixdesctest
acopy                   deflicker               pixelize
acrossfade              deinterlace_qsv         pixscope
acrossover              deinterlace_vaapi       pp7
acrusher                dejudder                premultiply
acue                    delogo                  premultiply_dynamic
addroi                  denoise_vaapi           prewitt
adeclick                deshake                 prewitt_opencl
adeclip                 deshake_opencl          procamp_vaapi
adecorrelate            despill                 program_opencl
adelay                  detelecine              pseudocolor
adenorm                 dialoguenhance          psnr
aderivative             dilation                pullup
adrawgraph              dilation_opencl         qp
adrc                    displace                qrencode
adynamicequalizer       doubleweave             qrencodesrc
adynamicsmooth          drawbox                 quirc
aecho                   drawbox_vaapi           random
aemphasis               drawgraph               readeia608
aeval                   drawgrid                readvitc
aevalsrc                drawtext                realtime
aexciter                drmeter                 remap
afade                   dynaudnorm              remap_opencl
afdelaysrc              earwax                  removegrain
afftdn                  ebur128                 removelogo
afftfilt                edgedetect              repeatfields
afir                    elbg                    replaygain
afireqsrc               entropy                 reverse
afirsrc                 epx                     rgbashift
aformat                 eq                      rgbtestsrc
afreqshift              equalizer               roberts
afwtdn                  erosion                 roberts_opencl
agate                   erosion_opencl          rotate
agraphmonitor           estdif                  rubberband
ahistogram              exposure                sab
aiir                    extractplanes           scale
aintegral               extrastereo             scale2ref
ainterleave             fade                    scale_cuda
alatency                feedback                scale_d3d11
alimiter                fftdnoiz                scale_qsv
allpass                 fftfilt                 scale_vaapi
allrgb                  field                   scale_vulkan
allyuv                  fieldhint               scdet
aloop                   fieldmatch              scdet_vulkan
alphaextract            fieldorder              scharr
alphamerge              fillborders             scroll
amerge                  find_rect               segment
ametadata               firequalizer            select
amix                    flanger                 selectivecolor
amovie                  flip_vulkan             sendcmd
amplify                 flite                   separatefields
amultiply               floodfill               setdar
anequalizer             format                  setfield
anlmdn                  fps                     setparams
anlmf                   framepack               setpts
anlms                   framerate               setrange
anoisesrc               framestep               setsar
anull                   freezedetect            settb
anullsink               freezeframes            sharpness_vaapi
anullsrc                frei0r                  shear
apad                    frei0r_src              showcqt
aperms                  fspp                    showcwt
aphasemeter             fsync                   showfreqs
aphaser                 gblur                   showinfo
aphaseshift             gblur_vulkan            showpalette
apsnr                   geq                     showspatial
apsyclip                gfxcapture              showspectrum
apulsator               gradfun                 showspectrumpic
arealtime               gradients               showvolume
aresample               graphmonitor            showwaves
areverse                grayworld               showwavespic
arls                    greyedge                shuffleframes
arnndn                  guided                  shufflepixels
asdr                    haas                    shuffleplanes
asegment                haldclut                sidechaincompress
aselect                 haldclutsrc             sidechaingate
asendcmd                hdcd                    sidedata
asetnsamples            headphone               sierpinski
asetpts                 hflip                   signalstats
asetrate                hflip_vulkan            signature
asettb                  highpass                silencedetect
ashowinfo               highshelf               silenceremove
asidedata               hilbert                 sinc
asisdr                  histeq                  sine
asoftclip               histogram               siti
aspectralstats          hqdn3d                  smartblur
asplit                  hqx                     smptebars
ass                     hstack                  smptehdbars
astats                  hstack_qsv              sobel
astreamselect           hstack_vaapi            sobel_opencl
asubboost               hsvhold                 sofalizer
asubcut                 hsvkey                  spectrumsynth
asupercut               hue                     speechnorm
asuperpass              huesaturation           split
asuperstop              hwdownload              spp
atadenoise              hwmap                   sr_amf
atempo                  hwupload                ssim
atilt                   hwupload_cuda           ssim360
atrim                   hysteresis              stereo3d
avectorscope            iccdetect               stereotools
avgblur                 iccgen                  stereowiden
avgblur_opencl          identity                streamselect
avgblur_vulkan          idet                    subtitles
avsynctest              il                      super2xsai
axcorrelate             inflate                 superequalizer
azmq                    interlace               surround
backgroundkey           interlace_vulkan        swaprect
bandpass                interleave              swapuv
bandreject              join                    tblend
bass                    kerndeint               telecine
bbox                    kirsch                  testsrc
bench                   ladspa                  testsrc2
bilateral               lagfun                  thistogram
bilateral_cuda          latency                 threshold
biquad                  lenscorrection          thumbnail
bitplanenoise           lensfun                 thumbnail_cuda
blackdetect             libplacebo              tile
blackdetect_vulkan      libvmaf                 tiltandshift
blackframe              life                    tiltshelf
blend                   limitdiff               tinterlace
blend_vulkan            limiter                 tlut2
blockdetect             loop                    tmedian
blurdetect              loudnorm                tmidequalizer
bm3d                    lowpass                 tmix
boxblur                 lowshelf                tonemap
boxblur_opencl          lumakey                 tonemap_opencl
bs2b                    lut                     tonemap_vaapi
bwdif                   lut1d                   tpad
bwdif_cuda              lut2                    transpose
bwdif_vulkan            lut3d                   transpose_opencl
cas                     lutrgb                  transpose_vaapi
ccrepack                lutyuv                  transpose_vulkan
cellauto                mandelbrot              treble
channelmap              maskedclamp             tremolo
channelsplit            maskedmax               trim
chorus                  maskedmerge             unpremultiply
chromaber_vulkan        maskedmin               unsharp
chromahold              maskedthreshold         unsharp_opencl
chromakey               maskfun                 untile
chromakey_cuda          mcdeint                 uspp
chromanr                mcompand                v360
chromashift             median                  vaguedenoiser
ciescope                mergeplanes             varblur
codecview               mestimate               vectorscope
color                   metadata                vflip
color_vulkan            midequalizer            vflip_vulkan
colorbalance            minterpolate            vfrdet
colorchannelmixer       mix                     vibrance
colorchart              monochrome              vibrato
colorcontrast           morpho                  vidstabdetect
colorcorrect            movie                   vidstabtransform
colordetect             mpdecimate              vif
colorhold               mptestsrc               vignette
colorize                msad                    virtualbass
colorkey                multiply                vmafmotion
colorkey_opencl         negate                  volume
colorlevels             nlmeans                 volumedetect
colormap                nlmeans_opencl          vpp_amf
colormatrix             nlmeans_vulkan          vpp_qsv
colorspace              nnedi                   vstack
colorspace_cuda         noformat                vstack_qsv
colorspectrum           noise                   vstack_vaapi
colortemperature        normalize               w3fdif
compand                 null                    waveform
compensationdelay       nullsink                weave
concat                  nullsrc                 whisper
convolution             openclsrc               xbr
convolution_opencl      oscilloscope            xcorrelate
convolve                overlay                 xfade
copy                    overlay_cuda            xfade_opencl
corr                    overlay_opencl          xfade_vulkan
cover_rect              overlay_qsv             xmedian
crop                    overlay_vaapi           xpsnr
cropdetect              overlay_vulkan          xstack
crossfeed               owdenoise               xstack_qsv
crystalizer             pad                     xstack_vaapi
cue                     pad_cuda                yadif
curves                  pad_opencl              yadif_cuda
datascope               pad_vaapi               yaepblur
dblur                   pal100bars              yuvtestsrc
dcshift                 pal75bars               zmq
dctdnoiz                palettegen              zoneplate
ddagrab                 paletteuse              zoompan
deband                  pan                     zscale

Enabled bsfs:
aac_adtstoasc           h264_metadata           pgs_frame_merge
ahx_to_mp2              h264_mp4toannexb        prores_metadata
apv_metadata            h264_redundant_pps      remove_extradata
av1_frame_merge         hapqa_extract           setts
av1_frame_split         hevc_metadata           showinfo
av1_metadata            hevc_mp4toannexb        smpte436m_to_eia608
chomp                   imx_dump_header         text2movsub
dca_core                media100_to_mjpegb      trace_headers
dovi_rpu                mjpeg2jpeg              truehd_core
dts2pts                 mjpega_dump_header      vp9_metadata
dump_extradata          mov2textsub             vp9_raw_reorder
dv_error_marker         mpeg2_metadata          vp9_superframe
eac3_core               mpeg4_unpack_bframes    vp9_superframe_split
eia608_to_smpte436m     noise                   vvc_metadata
evc_frame_merge         null                    vvc_mp4toannexb
extract_extradata       opus_metadata
filter_units            pcm_rechunk

Enabled indevs:
dshow                   lavfi                   openal
gdigrab                 libcdio                 vfwcap

Enabled outdevs:
caca

git-full external libraries' versions: 

AMF v1.4.36-4-g5e3b7df
aom v3.13.1-62-gb5e63b9bec
aribb24 v1.0.3-5-g5e9be27
aribcaption 1.1.1
AviSynthPlus v3.7.5-32-g805fda74
bs2b 3.1.0
chromaprint 1.6.0
codec2 1.2.0-106-g96e8a19c
dav1d 1.5.1-20-gaf5cf2b
davs2 1.7-1-gb41cf11
dvdnav 7.0.0
dvdread 7.0.0
ffnvcodec n13.0.19.0-2-g876af32
flite v2.2-55-g6c9f20d
freetype VER-2-14-1
frei0r v2.4.0-5-g62dd466
fribidi v1.0.16-2-gb28f43b
gsm 1.0.22
harfbuzz 12.1.0-1-gd1a3e403
ladspa-sdk 1.17
lame 3.100
lc3 1.1.3
lcms2 2.16
lensfun v0.3.95-1797-gde93b84f
libass 0.17.4-15-g534a5f8
libcdio-paranoia 10.2
libgme 0.6.4
libilbc v3.0.4-346-g6adb26d4a4
libjxl v0.11-snapshot-427-gffd01c98
libopencore-amrnb 0.1.6
libopencore-amrwb 0.1.6
libplacebo v7.351.0-80-g76c7076
libsoxr 0.1.3
libssh 0.11.2
libtheora v1.2.0
libwebp v1.6.0-104-g1f0a494
openal-soft latest
openapv v0.2.0.4
openmpt libopenmpt-0.6.25-9-gfad56bc0f
opus v1.5.2-214-g34bba701
qrencode 4.1.1
quirc 1.2
rav1e p20250624-1-gb7bf390
rist 0.2.12
rubberband v1.8.1
SDL release-2.32.0-112-g39a0fab4d
shaderc v2025.4
shine 3.1.1
snappy 1.2.2
speex Speex-1.2.1-51-g0589522
srt v1.5.5-rc.0a
SVT-AV1 v3.1.0-150-gcdee1494
twolame 0.4.0
uavs3d v1.1-47-g1fd0491
VAAPI 2.23.0.
vidstab v1.1.1-20-g4bd81e3
vmaf v3.0.0-113-g2b2cf9c1
vo-amrwbenc 0.1.3
vorbis v1.3.7-20-g43bbff01
VPL 2.15
vpx v1.15.2-126-g22206729a
vulkan-loader v1.4.328-5-gae0461b
vvenc v1.13.1-191-g716c59d
whisper.cpp 1.8.0
x264 v0.165.3223
x265 4.1-192-g10f529eaa
xavs2 1.4
xevd 0.5.0
xeve 0.5.1
xvid v1.3.7
zeromq 4.3.5
zimg release-3.0.6-211-gdf9c147
zvbi v0.2.44

