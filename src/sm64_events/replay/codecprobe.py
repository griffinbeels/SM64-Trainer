"""Once-per-startup capability check for the actual FFmpeg executable.

An encoder listed in a build may lack a compatible GPU/driver, or change our
VFR picture clock. Require a real encode and independently decode its output.
This small startup witness is not a throughput or visual-quality benchmark.
"""
import io
import logging
import subprocess

import av
import numpy as np

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.replay.config import (
    RING_MAXRATE, forced_idr_args, raw_picture_args, video_quality_args,
)
from sm64_events.replay.media import MEDIA_HZ, MEDIA_TIME_BASE, picture_duration_filter

log = logging.getLogger("sm64.replay")
HARDWARE_CODECS = ("h264_nvenc", "h264_amf", "h264_qsv")
_SIZE = (640, 480)  # Tiny frames can falsely reject NVENC's minimum encode size.
_PTS = (90000, 93000, 93001, 99000, 100500, 108000)
_TIMEOUT_S = 8


def _color(index: int) -> tuple[int, int, int]:
    return (40 + index * 25, 100, 220 - index * 20)  # RGB, different per picture


def probe_input() -> bytes:
    """Owned BGRA pictures with unequal PTS and asymmetric color witnesses."""
    output = io.BytesIO()
    with av.open(output, "w", format="nut") as container:
        # Same nominal header rate as the live NUT mux; picture PTS, not this
        # rate, control playback. Hardware must accept that transport too.
        stream = container.add_stream("rawvideo", rate=1000)
        stream.width, stream.height = _SIZE
        stream.pix_fmt = "bgra"
        stream.time_base = stream.codec_context.time_base = MEDIA_TIME_BASE
        for index, pts in enumerate(_PTS):
            pixels = np.empty((_SIZE[1], _SIZE[0], 4), dtype=np.uint8)
            pixels[:, :, 3] = 255
            pixels[:240, :, :3] = _color(index)[::-1]
            pixels[240:, :, :3] = _color(index)
            packet = av.Packet(memoryview(pixels))
            packet.stream = stream
            packet.pts = packet.dts = pts
            packet.time_base = MEDIA_TIME_BASE
            packet.is_keyframe = True
            container.mux(packet)
    return output.getvalue()


def validate_output(data: bytes) -> None:
    """Reject loss, reordering, retiming, scaling, flips and swapped channels."""
    with av.open(io.BytesIO(data), format="mpegts") as container:
        if len(container.streams.video) != 1:
            raise ValueError("expected one video stream")
        stream = container.streams.video[0]
        stream.codec_context.thread_count = 1
        count = 0
        for index, frame in enumerate(container.decode(stream)):
            if (index >= len(_PTS) or frame.pts is None or frame.time_base is None
                    or frame.pts * frame.time_base * MEDIA_HZ != _PTS[index]):
                raise ValueError("picture count/order/timestamps changed")
            if (frame.width, frame.height) != _SIZE:
                raise ValueError("picture dimensions changed")
            if index == 0 and not frame.key_frame:
                raise ValueError("first picture is not independently decodable")
            rgb = frame.to_ndarray(format="rgb24")
            # Far from the boundary, tolerate lossy H.264 color rounding only.
            for y, expected in ((120, _color(index)), (360, _color(index)[::-1])):
                actual = rgb[y, 320].astype(int)
                if np.max(np.abs(actual - expected)) > 12:
                    raise ValueError(f"picture {index} color changed at y={y}: "
                                     f"expected {expected}, decoded {tuple(actual)}")
            count += 1
        if count != len(_PTS):
            raise ValueError(f"expected {len(_PTS)} pictures, decoded {count}")


def check_ffmpeg_codec(ffmpeg: str, codec: str, source: bytes) -> None:
    """Bounded hidden child; raise on unsupported hardware or invalid output."""
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-copyts",
            "-f", "nut", "-i", "pipe:0", "-map", "0:v:0", "-an",
            "-c:v", codec, *video_quality_args(codec, "realtime", RING_MAXRATE),
            *raw_picture_args(codec),
            "-bf", "0", "-g", "60", "-force_key_frames", "expr:gte(t,n_forced*0.1)",
            *forced_idr_args(codec), "-fps_mode", "passthrough",
            "-enc_time_base", "demux", "-bsf:v", picture_duration_filter(),
            "-avoid_negative_ts", "disabled", "-mpegts_copyts", "1",
            "-f", "mpegts", "pipe:1"]
    result = subprocess.run(args, input=source, capture_output=True, check=False,
                            timeout=_TIMEOUT_S, **quiet_spawn_kwargs())
    if result.returncode:
        reason = result.stderr.decode(errors="replace")[-1200:].strip()
        raise RuntimeError(f"FFmpeg exited {result.returncode}: {reason}")
    validate_output(result.stdout)


def pick_ffmpeg_codec(ffmpeg: str) -> str:
    """Prefer NVIDIA, then AMD, then Intel hardware; keep x264 as fallback."""
    try:
        source = probe_input()
    except Exception:
        log.exception("replay hardware probe input failed; using libx264")
        return "libx264"
    for codec in HARDWARE_CODECS:
        try:
            check_ffmpeg_codec(ffmpeg, codec, source)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError,
                av.error.FFmpegError) as exc:
            log.info("replay encoder %s rejected: %s", codec, exc)
        else:
            log.info("replay encoder: %s (FFmpeg picture clock/output probe passed)", codec)
            return codec
    log.info("no compatible hardware encoder passed; using libx264")
    return "libx264"
