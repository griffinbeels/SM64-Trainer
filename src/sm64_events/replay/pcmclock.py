"""Shared sample-preserving PCM placement on a replay's first-picture clock."""


def trim_to_origin(
    pcm: bytes, first_sample_us: int, origin_ts: float, rate: int,
) -> tuple[bytes, int] | None:
    """Return packed s16le stereo and its relative microsecond timestamp.

    Drop only samples before the first picture. Retain the established integer
    ceil and microsecond rounding so both packet mux routes use the same clock.
    No copy occurs for a wholly post-origin block.
    """
    samples = len(pcm) // 4
    if samples <= 0:
        return None
    if len(pcm) % 4:
        raise ValueError("PCM must contain complete s16le stereo samples")
    relative = int(first_sample_us) - int(round(origin_ts * 1_000_000))
    if relative < 0:
        skip = min(samples, (-relative * rate + 999_999) // 1_000_000)
        pcm = pcm[skip * 4:]
        if not pcm:
            return None
        relative += round(skip * 1_000_000 / rate)
    return pcm, relative
