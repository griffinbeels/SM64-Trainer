"""The source-audio layer of a scene package: bytes in, refusals out (M4 E4-audio).

A layer payload carries one recording's isolated game audio and the clock
that ties it to the package's pictures (audio_clock.py). It is self-contained
and position-independent, so package.py can store it as one more layer
("source-audio", version 1) without knowing its insides, and a viewer can
open it with no host state. It holds no strings, paths or absolute host
clock values: picture and segment times are ticks after the first picture.

Format v1, little-endian: a 136-byte header, the index tables, then the audio
blocks back to back.

    header   magic "SM64AUD\\0", version, codec, rate, channels, bits, clock
             frequency, total samples, block samples, block/segment/loss/
             picture counts, the picture-time offset used for mapping, index
             bytes, sha256 of the index, sha256 of the whole decoded stream
    blocks   (offset, stored bytes, samples, sha256 of the decoded s16 PCM)
             tiling the stream in order; every block decodes alone
    segments (epoch, flags, first sample in its epoch, samples, first sample
             in the stream, anchor tick, ticks per sample * 2**32), tiling
             the stream in order with non-decreasing anchors
    losses   (first sample in the stream, samples) whose content the fanout
             could not keep, stored as silence and marked, never hidden
    pictures (tap sequence, status, presentation tick, stream sample or -1)

Codec FLAC stores each block as raw FLAC frames (4608 samples each); a block
plus the STREAMINFO this module builds from the header is a complete .flac
file, which browsers decode natively (decodeAudioData). STORED keeps raw s16
PCM. A reader refuses an unknown version or codec, any size past its bounds,
any table that does not tile or order, any picture whose sample the segment
parameters do not reproduce, and any block whose decoded bytes miss their
hash.
"""
from __future__ import annotations

import hashlib
import io
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio_clock as ac  # noqa: E402

MAGIC = b"SM64AUD\x00"
VERSION = 1
LAYER = ("source-audio", 1)
STORED, FLAC = 0, 1
CODECS = {"stored": STORED, "flac": FLAC}
FLAC_FRAME = 4608
HEADER = struct.Struct("<8sHHIHHQQIIIIIqII32s32s")
BLOCK = struct.Struct("<QII32s")          # offset in the payload, stored bytes, samples, sha256 of decoded PCM
SEGMENT = struct.Struct("<IIQQQqQ")       # epoch, flags, first, samples, stream first, anchor, period_q32
LOSS = struct.Struct("<QQ")               # stream first, samples
PICTURE = struct.Struct("<IIqq")          # tap sequence, status, ticks, stream sample or -1

MAX_SECONDS = 3 * 3600
MAX_BLOCK_SAMPLES = FLAC_FRAME * 256
MAX_SEGMENTS = 1 << 16
MAX_LOSSES = 1 << 16
MAX_PICTURES = 1 << 24
MAX_PPM = ac.MAX_PPM


class LayerError(ValueError):
    """The audio layer is malformed, corrupt, or out of bounds."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise LayerError(message)


def streaminfo(rate: int, channels: int, bits: int) -> bytes:
    """The STREAMINFO a block's frames decode under: fixed 4608-sample frames,
    frame sizes, total length and MD5 left unknown (zero) as FLAC allows."""
    packed = (rate << 44) | ((channels - 1) << 41) | ((bits - 1) << 36)
    return struct.pack(">HH", FLAC_FRAME, FLAC_FRAME) + bytes(6) + packed.to_bytes(8, "big") + bytes(16)


def flac_file(block: bytes, rate: int, channels: int = 2, bits: int = 16) -> bytes:
    """A block as a complete .flac file (what a browser's decodeAudioData takes)."""
    return b"fLaC" + bytes((0x80, 0, 0, 34)) + streaminfo(rate, channels, bits) + block


def _flac_encode(pcm: np.ndarray, rate: int, level: int) -> bytes:
    import av

    ctx = av.CodecContext.create("flac", "w")
    ctx.sample_rate, ctx.layout, ctx.format = rate, "stereo", "s16"
    ctx.options = {"compression_level": str(level)}
    ctx.open()
    if ctx.frame_size != FLAC_FRAME:
        raise LayerError(f"FLAC encoder frame size {ctx.frame_size}, expected {FLAC_FRAME}")
    frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(pcm).reshape(1, -1), format="s16", layout="stereo")
    frame.sample_rate, frame.pts = rate, 0
    return b"".join(bytes(p) for p in [*ctx.encode(frame), *ctx.encode(None)])


def _flac_decode(block: bytes, rate: int, samples: int) -> np.ndarray:
    import av

    out, held = [], 0
    try:
        with av.open(io.BytesIO(flac_file(block, rate)), format="flac") as box:
            for frame in box.decode(audio=0):
                _require(frame.format.name == "s16" and frame.layout.nb_channels == 2
                         and frame.sample_rate == rate, "FLAC block decodes to another format")
                data = frame.to_ndarray().reshape(-1, 2)
                held += len(data)
                _require(held <= samples, "FLAC block decodes past its declared samples")
                out.append(data)
    except av.FFmpegError as exc:
        raise LayerError(f"FLAC block does not decode: {exc}") from exc
    _require(held == samples, "FLAC block decodes short of its declared samples")
    return np.concatenate(out) if out else np.zeros((0, 2), np.int16)


def encode_layer(clock: ac.AudioClock, pcm: np.ndarray, *, codec: str = "flac", block_frames: int = 20,
                 level: int = 8) -> bytes:
    """The layer payload for `clock` and its stored stream (audio_clock.stream_pcm).

    Every FLAC block is decoded back and compared before it is kept: a codec
    that is not lossless here is a writer fault, not a viewer surprise."""
    _require(codec in CODECS, f"unknown audio codec {codec!r}")
    _require(clock.rate * MAX_SECONDS >= clock.total_samples, "recording longer than the layer allows")
    _require(isinstance(pcm, np.ndarray) and pcm.dtype == np.int16 and pcm.shape == (clock.total_samples, 2),
             "stored stream must be (total samples, 2) int16")
    block_samples = FLAC_FRAME * block_frames
    _require(FLAC_FRAME <= block_samples <= MAX_BLOCK_SAMPLES, "block size out of range")
    blocks, data = [], []
    for at in range(0, clock.total_samples, block_samples):
        part = np.ascontiguousarray(pcm[at:at + block_samples])
        raw = part.tobytes()
        if CODECS[codec] == FLAC:
            stored = _flac_encode(part, clock.rate, level)
            _require(np.array_equal(_flac_decode(stored, clock.rate, len(part)), part), "FLAC round trip differs")
        else:
            stored = raw
        blocks.append((len(stored), len(part), hashlib.sha256(raw).digest()))
        data.append(stored)
    segments = [(s.epoch, s.flags, s.first, s.samples, s.stream_first, s.anchor, s.period_q32)
                for s in clock.segments]
    pictures = [(p.sequence, p.status, p.ticks, p.sample) for p in clock.pictures]
    index_bytes = (len(blocks) * BLOCK.size + len(segments) * SEGMENT.size + len(clock.losses) * LOSS.size
                   + len(pictures) * PICTURE.size)
    offset = HEADER.size + index_bytes
    index = bytearray()
    for stored, samples, digest in blocks:
        index += BLOCK.pack(offset, stored, samples, digest)
        offset += stored
    for row in segments:
        index += SEGMENT.pack(*row)
    for row in clock.losses:
        index += LOSS.pack(*row)
    for row in pictures:
        index += PICTURE.pack(*row)
    header = HEADER.pack(MAGIC, VERSION, CODECS[codec], clock.rate, 2, 16, clock.frequency,
                         clock.total_samples, block_samples, len(blocks), len(segments), len(clock.losses),
                         len(pictures), clock.offset_ticks, index_bytes, 0, hashlib.sha256(index).digest(),
                         hashlib.sha256(pcm.tobytes()).digest())
    payload = header + bytes(index) + b"".join(data)
    read_layer(payload)        # the writer refuses what a reader would
    return payload


@dataclass(frozen=True)
class Layer:
    """A parsed, checked layer over its payload; audio decodes on demand."""
    payload: bytes
    codec: int
    rate: int
    frequency: int
    total_samples: int
    block_samples: int
    offset_ticks: int
    pcm_sha256: bytes
    blocks: list            # (offset, stored, samples, sha256)
    segments: list          # audio_clock.Segment
    losses: list            # (stream first, samples)
    pictures: list          # audio_clock.Picture

    def block(self, index: int) -> np.ndarray:
        offset, stored, samples, digest = self.blocks[index]
        raw = self.payload[offset:offset + stored]
        if self.codec == FLAC:
            pcm = _flac_decode(raw, self.rate, samples)
        else:
            _require(len(raw) == samples * 4, "stored block length differs from its samples")
            pcm = np.frombuffer(raw, np.int16).reshape(-1, 2)
        _require(hashlib.sha256(pcm.tobytes()).digest() == digest, f"audio block {index} fails its hash")
        return pcm

    def pcm(self, first: int, count: int) -> np.ndarray:
        """Samples [first, first + count) of the stored stream, decoding only their blocks."""
        _require(0 <= first and 0 <= count and first + count <= self.total_samples, "audio range out of bounds")
        if not count:
            return np.zeros((0, 2), np.int16)
        lo, hi = first // self.block_samples, (first + count - 1) // self.block_samples
        joined = np.concatenate([self.block(i) for i in range(lo, hi + 1)])
        start = first - lo * self.block_samples
        return joined[start:start + count]

    def verify(self) -> None:
        """Decode every block and check the whole stream's hash."""
        digest = hashlib.sha256()
        for i in range(len(self.blocks)):
            digest.update(self.block(i).tobytes())
        _require(digest.digest() == self.pcm_sha256, "decoded audio stream fails its hash")


def _table(raw: bytes, at: int, fmt: struct.Struct, count: int) -> tuple[list, int]:
    end = at + fmt.size * count
    _require(end <= len(raw), "audio layer index truncated")
    return [fmt.unpack_from(raw, at + fmt.size * i) for i in range(count)], end


def read_layer(payload: bytes) -> Layer:
    """Parse and check a layer payload; raises LayerError on anything malformed."""
    _require(isinstance(payload, (bytes, bytearray)) and len(payload) >= HEADER.size, "audio layer truncated")
    payload = bytes(payload)
    (magic, version, codec, rate, channels, bits, frequency, total, block_samples, n_blocks, n_segments,
     n_losses, n_pictures, offset_ticks, index_bytes, _reserved, index_sha, pcm_sha) = HEADER.unpack_from(payload)
    _require(magic == MAGIC, "not an audio layer")
    _require(version == VERSION, f"unknown audio layer version {version}")
    _require(codec in CODECS.values(), f"unknown audio codec {codec}")
    _require((channels, bits) == (2, 16), "audio layer must be 16-bit stereo")
    _require(8000 <= rate <= 192000 and 1000 <= frequency <= 10**10, "audio clock out of range")
    _require(total <= rate * MAX_SECONDS, "audio layer longer than allowed")
    _require(FLAC_FRAME <= block_samples <= MAX_BLOCK_SAMPLES and block_samples % FLAC_FRAME == 0,
             "audio block size out of range")
    _require(n_blocks == -(-total // block_samples), "audio block count does not cover the stream")
    _require(n_segments <= MAX_SEGMENTS and n_losses <= MAX_LOSSES and n_pictures <= MAX_PICTURES,
             "audio layer tables out of bounds")
    _require(abs(offset_ticks) <= frequency * 10, "audio offset out of range")
    expected = (n_blocks * BLOCK.size + n_segments * SEGMENT.size + n_losses * LOSS.size
                + n_pictures * PICTURE.size)
    _require(index_bytes == expected and HEADER.size + index_bytes <= len(payload), "audio layer index size")
    index = payload[HEADER.size:HEADER.size + index_bytes]
    _require(hashlib.sha256(index).digest() == index_sha, "audio layer index fails its hash")

    blocks, at = _table(index, 0, BLOCK, n_blocks)
    rows, at = _table(index, at, SEGMENT, n_segments)
    losses, at = _table(index, at, LOSS, n_losses)
    picture_rows, at = _table(index, at, PICTURE, n_pictures)
    _check_blocks(blocks, block_samples, total, HEADER.size + index_bytes, len(payload))
    segments = _segments(rows, rate, frequency, total)
    losses = [tuple(row) for row in losses]
    end = 0
    for i, (first, samples) in enumerate(losses):
        _require(samples > 0 and first >= end and first + samples <= total, f"audio loss {i} out of order")
        end = first + samples
    pictures = _pictures(picture_rows, segments, losses, offset_ticks)
    return Layer(payload, codec, rate, frequency, total, block_samples, offset_ticks, pcm_sha, blocks,
                 segments, losses, pictures)


def _check_blocks(blocks: list, block_samples: int, total: int, offset: int, length: int) -> None:
    raw_limit = block_samples * 4 + 64 * (block_samples // FLAC_FRAME) + 4096
    for i, (start, stored, samples, _digest) in enumerate(blocks):
        _require(start == offset, f"audio block {i} is not where the previous one ends")
        _require(samples == min(block_samples, total - i * block_samples), f"audio block {i} sample count")
        _require(0 < stored <= raw_limit, f"audio block {i} stored size out of range")
        offset += stored
    _require(offset == length, "audio layer length differs from its blocks")


def _segments(rows: list, rate: int, frequency: int, total: int) -> list:
    period = frequency / rate
    segments, stream = [], 0
    for i, (epoch, flags, first, samples, stream_first, anchor, period_q32) in enumerate(rows):
        _require(flags & ~(ac.MEASURED | ac.JOINED) == 0, f"audio segment {i} flags")
        _require(samples > 0 and stream_first == stream, f"audio segment {i} does not tile the stream")
        _require(abs(period_q32 / (1 << ac.Q) / period - 1) * 1e6 <= MAX_PPM, f"audio segment {i} rate")
        segment = ac.Segment(epoch, first, samples, stream_first, anchor, period_q32, flags)
        if i:
            prev = segments[-1]
            _require(epoch >= prev.epoch and anchor >= prev.anchor, f"audio segment {i} out of order")
            _require(not flags & ac.JOINED or (epoch == prev.epoch and first == prev.first + prev.samples),
                     f"audio segment {i} joins a segment it does not follow")
        else:
            _require(not flags & ac.JOINED, "the first audio segment cannot join one")
        segments.append(segment)
        stream += samples
    _require(stream == total, "audio segments do not cover the stream")
    return segments


def _pictures(rows: list, segments: list, losses: list, offset_ticks: int) -> list:
    pictures = []
    for i, (sequence, status, ticks, sample) in enumerate(rows):
        _require(status < len(ac.STATUS_NAMES), f"audio picture {i} status")
        if i:
            _require(sequence > pictures[-1].sequence and ticks >= pictures[-1].ticks,
                     f"audio picture {i} out of order")
        # Every picture's sample re-derives from the stored clock alone.
        _require(ac.locate(ticks + offset_ticks, segments, losses) == (status, sample),
                 f"audio picture {i} does not follow from the stored clock")
        pictures.append(ac.Picture(sequence, ticks, status, sample))
    return pictures
