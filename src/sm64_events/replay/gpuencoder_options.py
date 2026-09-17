"""Explicit resolved options; replay.config remains the sole quality owner."""

import re
from .gpuencoder_abi import Options, initialize


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

    args = config.video_quality_args("h264_nvenc", "realtime", config.RING_MAXRATE)
    if len(args) % 2:
        raise ValueError("unsupported quality argument shape")
    quality = dict(zip(args[::2], args[1::2], strict=True))
    required = {
        "-preset",
        "-tune",
        "-profile:v",
        "-rc",
        "-cq",
        "-b:v",
        "-maxrate",
        "-bufsize",
    }
    if set(quality) != required or quality["-b:v"] != "0":
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
            profile={"high": 100}[quality["-profile:v"]],
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
