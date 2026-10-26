"""Measure source-audio encodings on saved replays (M4 E4-audio, read-only).

    audio_bench.py <clip.mp4 | folder>... [--limit N] [--out result.json]

Each clip's game audio (the recorder's AAC track) is decoded to the 48 kHz
s16 stereo PCM the scene layer would store, then encoded with every
candidate: FLAC through audio_layer.py (the layer's own blocks), WavPack,
numpy delta + lzma (a pure-Python lossless baseline), and Opus 128 kb/s as a
lossy reference beside the AAC already stored. Sizes are reported per minute
of audio with encode/decode CPU seconds per minute.

Caveat stated with every result: decoded AAC stands in for the tap's own PCM.
AAC is band-limited like the source (SM64 mixes at 32 kHz, so nothing lies
above 16 kHz) but adds its own quantization noise, so the lossless sizes are
estimates until a live session feeds the fanout's PCM through this tool.
Clips are opened read-only and never written.
"""
from __future__ import annotations

import argparse
import json
import lzma
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio_layer as al  # noqa: E402

RATE = 48000


def decode_track(path: Path) -> tuple[np.ndarray, int]:
    """(PCM (n, 2) int16 at 48 kHz, stored AAC bytes) of a clip's audio track."""
    import av

    with av.open(str(path), mode="r") as box:
        stream = box.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="stereo", rate=RATE)
        parts, stored = [], 0
        for packet in box.demux(stream):
            stored += packet.size
            for frame in packet.decode():
                for out in resampler.resample(frame):
                    parts.append(out.to_ndarray().reshape(-1, 2))
        for out in resampler.resample(None):
            parts.append(out.to_ndarray().reshape(-1, 2))
    return (np.concatenate(parts) if parts else np.zeros((0, 2), np.int16)), stored


def _timed(fn, *args):
    start = time.process_time()
    result = fn(*args)
    return result, time.process_time() - start


def flac_blocks(pcm: np.ndarray, level: int) -> list[bytes]:
    step = al.FLAC_FRAME * 20
    return [al._flac_encode(np.ascontiguousarray(pcm[at:at + step]), RATE, level) for at in range(0, len(pcm), step)]


def flac_decode(blocks: list[bytes], pcm: np.ndarray) -> None:
    step = al.FLAC_FRAME * 20
    for i, block in enumerate(blocks):
        part = pcm[i * step:(i + 1) * step]
        if not np.array_equal(al._flac_decode(block, RATE, len(part)), part):
            raise SystemExit("FLAC round trip differs")


def codec_bytes(pcm: np.ndarray, codec: str, bit_rate: int | None = None) -> bytes:
    import av

    ctx = av.CodecContext.create(codec, "w")
    ctx.sample_rate, ctx.layout = RATE, "stereo"
    ctx.format = "s16p" if codec == "wavpack" else "s16"     # PyAV converts the packed frames losslessly
    if bit_rate:
        ctx.bit_rate = bit_rate
    ctx.open()
    frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(pcm).reshape(1, -1), format="s16", layout="stereo")
    frame.sample_rate, frame.pts = RATE, 0
    return b"".join(bytes(p) for p in [*ctx.encode(frame), *ctx.encode(None)])


def delta_lzma(pcm: np.ndarray) -> bytes:
    """Per-channel first differences, planar, lzma preset 6: portable, no codec library."""
    planar = pcm.T.astype(np.int32)
    deltas = np.diff(planar, axis=1, prepend=0).astype(np.int16)
    return lzma.compress(deltas.tobytes(), preset=6)


def silent_fraction(pcm: np.ndarray) -> float:
    """Share of 10 ms windows whose peak is at most 2 LSB."""
    windows = len(pcm) // 480
    if not windows:
        return 0.0
    peaks = np.abs(pcm[:windows * 480].reshape(windows, -1).astype(np.int32)).max(axis=1)
    return float((peaks <= 2).mean())


def measure(path: Path) -> dict:
    pcm, aac = decode_track(path)
    minutes = len(pcm) / RATE / 60
    row = {"clip": path.name, "seconds": round(len(pcm) / RATE, 2), "silent": round(silent_fraction(pcm), 3),
           "bytes": {"pcm_s16": pcm.nbytes, "aac_160k_stored": aac}, "cpu_s": {}}
    for level in (5, 8):
        blocks, enc = _timed(flac_blocks, pcm, level)
        _, dec = _timed(flac_decode, blocks, pcm)
        row["bytes"][f"flac_{level}"] = sum(map(len, blocks))
        row["cpu_s"][f"flac_{level}"] = {"encode": enc, "decode": dec}
    data, enc = _timed(codec_bytes, pcm, "wavpack")
    row["bytes"]["wavpack"], row["cpu_s"]["wavpack"] = len(data), {"encode": enc}
    data, enc = _timed(delta_lzma, pcm)
    _, dec = _timed(lzma.decompress, data)
    row["bytes"]["delta_lzma"], row["cpu_s"]["delta_lzma"] = len(data), {"encode": enc, "decode": dec}
    data, enc = _timed(codec_bytes, pcm, "libopus", 128000)
    row["bytes"]["opus_128k"], row["cpu_s"]["opus_128k"] = len(data), {"encode": enc}
    row["mb_per_min"] = {k: round(v / 1e6 / minutes, 3) for k, v in row["bytes"].items()} if minutes else {}
    row["cpu_s_per_min"] = {k: {kk: round(vv / minutes, 3) for kk, vv in v.items()}
                            for k, v in row["cpu_s"].items()} if minutes else {}
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out")
    args = parser.parse_args()
    clips = []
    for raw in args.paths:
        path = Path(raw)
        clips += sorted(path.rglob("*.mp4")) if path.is_dir() else [path]
    clips = [c for c in clips if not any(part.startswith(".") for part in c.parts[-3:-1])]
    if args.limit:
        clips = clips[-args.limit:]
    rows = [measure(clip) for clip in clips]
    seconds = sum(r["seconds"] for r in rows)
    total = {k: sum(r["bytes"][k] for r in rows) for k in rows[0]["bytes"]} if rows else {}
    summary = {
        "clips": len(rows), "minutes": round(seconds / 60, 2),
        "caveat": "decoded AAC stands in for the tap's PCM; lossless sizes are estimates",
        "mb_per_min_overall": {k: round(v / 1e6 / (seconds / 60), 3) for k, v in total.items()} if seconds else {},
        "mb_per_min_median": {k: round(statistics.median(r["mb_per_min"][k] for r in rows), 3) for k in total},
        "mb_per_min_max": {k: round(max(r["mb_per_min"][k] for r in rows), 3) for k in total},
        "cpu_s_per_min_overall": {
            k: {kk: round(sum(r["cpu_s"][k][kk] for r in rows) / (seconds / 60), 3) for kk in rows[0]["cpu_s"][k]}
            for k in rows[0]["cpu_s"]} if rows else {},
        "silent_median": round(statistics.median(r["silent"] for r in rows), 3) if rows else None,
    }
    result = {"summary": summary, "clips": [{k: v for k, v in r.items() if k != "cpu_s"} for r in rows]}
    text = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
