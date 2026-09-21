"""Explicit resolved options; replay.config remains the sole quality owner."""

import re
from .gpuencoder_abi import CODEC_AV1, CODEC_H264, Options, initialize

# Which ffmpeg encoder row in the quality registry describes each native codec,
# and which of its flags the native encoder can actually represent. AV1 NVENC
# has exactly one profile, so the registry never spells `-profile:v` for it and
# the resolver supplies the codec's own constant rather than a quality choice.
NVENC_ENCODER = {CODEC_H264: "h264_nvenc", CODEC_AV1: "av1_nvenc"}
QUALITY_FLAGS = {
    CODEC_H264: {"-preset", "-tune", "-profile:v", "-rc", "-cq", "-b:v",
                 "-maxrate", "-bufsize"},
    CODEC_AV1: {"-preset", "-tune", "-rc", "-cq", "-b:v", "-maxrate",
                "-bufsize"},
}
H264_PROFILES = {"high": 100}
AV1_MAIN_PROFILE = 0  # AV1 seq_profile 0; NVENC encodes no other.


def uint(value, bits, name, *, positive=False):
    if type(value) is not int or value < int(positive) or value >= 1 << bits:
        raise ValueError(
            f"{name} must fit unsigned {bits}-bit"
            + (" and be positive" if positive else "")
        )
    return value


def pod_options(resolved):
    names = {n for n, _ in Options._fields_} - {"struct_size", "version", "reserved"}
    if set(resolved) != names:
        raise ValueError(
            f"explicit encoder fields required; missing={sorted(names - set(resolved))}, extra={sorted(set(resolved) - names)}"
        )
    result = initialize(Options)
    for name, value in resolved.items():
        setattr(
            result, name, uint(value, 64 if name == "idr_interval_ticks" else 32, name)
        )
    if not 0 < result.max_packet_bytes <= 16 * 1024 * 1024:
        raise ValueError("packet byte bound outside native limit")
    return result


def from_replay_config(
    *,
    codec,
    width,
    height,
    nominal_fps_num,
    nominal_fps_den,
    b_frames,
    gop_frames,
    initial_qp_p,
    initial_qp_i,
    initial_qp_b,
    idr_interval_ticks,
    input_format,
    signal_color,
    full_range,
    matrix,
    primaries,
    transfer,
    max_packet_bytes,
):
    # Caller must resolve structural/rate/conversion values from qualified evidence.
    from sm64_events.replay import config

    if codec not in NVENC_ENCODER:
        raise ValueError("unsupported native codec")
    args = config.video_quality_args(
        NVENC_ENCODER[codec], "realtime", config.RING_MAXRATE
    )
    if len(args) % 2:
        raise ValueError("unsupported quality argument shape")
    quality = dict(zip(args[::2], args[1::2], strict=True))
    if set(quality) != QUALITY_FLAGS[codec] or quality["-b:v"] != "0":
        raise ValueError(
            "native encoder cannot represent current quality configuration"
        )

    def bitrate(text):
        match = re.fullmatch(r"([0-9]+)([kKmM]?)", text)
        if not match:
            raise ValueError("unsupported bitrate unit")
        return int(match[1]) * ({"": 1, "k": 1000, "m": 1000000}[match[2].lower()])

    try:
        resolved = dict(
            codec=codec,
            width=width,
            height=height,
            nominal_fps_num=nominal_fps_num,
            nominal_fps_den=nominal_fps_den,
            b_frames=b_frames,
            gop_frames=gop_frames,
            initial_qp_p=initial_qp_p,
            initial_qp_i=initial_qp_i,
            initial_qp_b=initial_qp_b,
            idr_interval_ticks=idr_interval_ticks,
            input_format=input_format,
            signal_color=signal_color,
            full_range=full_range,
            matrix=matrix,
            primaries=primaries,
            transfer=transfer,
            max_packet_bytes=max_packet_bytes,
            preset={"p4": 4}[quality["-preset"]],
            tuning={"hq": 1}[quality["-tune"]],
            profile=(
                H264_PROFILES[quality["-profile:v"]]
                if codec == CODEC_H264
                else AV1_MAIN_PROFILE
            ),
            rate_control={"vbr": 1}[quality["-rc"]],
            cq=int(quality["-cq"]),
            max_bitrate=bitrate(quality["-maxrate"]),
            vbv_buffer_bits=bitrate(quality["-bufsize"]),
        )
    except (KeyError, ValueError) as exc:
        raise ValueError(
            "native encoder cannot represent current quality configuration"
        ) from exc
    pod_options(resolved)
    return resolved
