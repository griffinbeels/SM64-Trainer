"""The scene viewer's sound (M4 E4-audio, lab only): an open package's source-audio layer as the page plays it.

    sound = SceneSound.of(package, presents)     # None when the package carries no sound
    sound.describe()                             # the page's table (below)
    data, media_type = sound.block(index)        # one block as a file the browser decodes

`presents` is the tap present sequence of each viewer frame (package.outline's "present"). A
frame's sample is the one sounding at its present through the layer's stored clock
(audio_clock.locate), so the page needs no clock of its own: it plays sample S[k] when frame k
appears and follows the pictures' own clock between frames.

Timing is "as heard": a frame plays the sound the runner heard while its picture was on screen,
Project64's own audio delay included. SOUND_SHIFT_MS is the one place that changes that: set to
Project64's measured delay, each sound plays with its game event instead. Griffin has not chosen
yet, and the next live session measures the delay.

describe() gives {available, rate, block_samples, total_samples, blocks, codec, shift_ms, samples,
lost}: `samples` per frame (-1 where the frame's picture has no sound: before or after the
recording, or in a gap), `lost` the frames whose sound span (to the next frame's sample) touches
a marked loss. The stored stream holds silence there, so it plays as silence, and the page says
so. A block is served whole, as the .flac file the layer's block makes with its STREAMINFO (or a
.wav for a stored layer), once its decoded PCM has matched its hash; a block that does not is
refused, never served.
"""
from __future__ import annotations

import bisect
import io
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio_clock as ac  # noqa: E402
import audio_layer as al  # noqa: E402

SOUND_SHIFT_MS = 0.0          # 0: as heard. Project64's audio delay here: each sound with its game event
NO_SOUND = "This recording has no sound."


def absent() -> dict:
    """describe() for a package without a source-audio layer."""
    return {"available": False, "reason": NO_SOUND}


class SceneSound:
    """One package's sound, mapped to the viewer's frames."""

    def __init__(self, layer: al.Layer, samples: list[int], lost: list[int], shift_ms: float):
        self.layer, self.samples, self.lost, self.shift_ms = layer, samples, lost, shift_ms
        self.checked: set[int] = set()            # blocks whose PCM matched its hash

    @classmethod
    def of(cls, package, presents: list[int | None], shift_ms: float = SOUND_SHIFT_MS) -> SceneSound | None:
        layer = package.audio
        if layer is None:
            return None
        pictures = {picture.sequence: picture for picture in layer.pictures}
        shift = round(shift_ms * layer.frequency / 1000)
        samples = []
        for present in presents:
            picture = pictures.get(present)
            if picture is None:
                samples.append(-1)
                continue
            status, sample = ac.locate(picture.ticks + layer.offset_ticks + shift, layer.segments, layer.losses)
            samples.append(sample if status in (ac.COVERED, ac.LOSS) else -1)
        return cls(layer, samples, lost_frames(samples, layer.losses), shift_ms)

    def describe(self) -> dict:
        layer = self.layer
        return {"available": True, "rate": layer.rate, "block_samples": layer.block_samples,
                "total_samples": layer.total_samples, "blocks": len(layer.blocks),
                "codec": "flac" if layer.codec == al.FLAC else "stored", "shift_ms": self.shift_ms,
                "samples": self.samples, "lost": self.lost}

    def block(self, index: int) -> tuple[bytes, str]:
        """Block `index` as a file the browser decodes: LookupError for no such block, LayerError
        (a ValueError) when its decoded PCM misses its hash. Thread-safe: the layer is immutable."""
        layer = self.layer
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(layer.blocks):
            raise LookupError("This recording's sound has no such block.")
        if index not in self.checked:
            layer.block(index)                                   # decodes and checks the hash
            self.checked.add(index)
        offset, stored, _samples, _digest = layer.blocks[index]
        raw = layer.payload[offset:offset + stored]
        if layer.codec == al.FLAC:
            return al.flac_file(raw, layer.rate), "audio/flac"
        out = io.BytesIO()
        with wave.open(out, "wb") as file:
            file.setnchannels(2)
            file.setsampwidth(2)
            file.setframerate(layer.rate)
            file.writeframes(raw)
        return out.getvalue(), "audio/wav"


class LiveSound:
    """A growing attempt scene's sound, from the recorder's own ring (live_audio.LiveAudio) rather
    than a layer in its package, which gains one only after the scene is whole: so the scene
    plays with sound from its first picture while it grows, and keeps this sound until it
    closes rather than switching mid-play. `clock` is the package index's "clock" (the QPC its
    present times count from, and the QPC's frequency). build() fits the ring's chunks to the
    frames' presents so far (live_audio.span_layer, uncompressed: measured 2026-10-26 on a
    synthetic 30 s recording, 18 ms a refit; 120 s, 75 ms); the chunks start at a fixed margin
    before the first picture, so the stream only grows and a block the page holds stays valid,
    while a refit may move earlier frames' samples by the fit's own error (about 1 ms)."""

    def __init__(self, live, clock: dict):
        self.live, self.origin, self.frequency = live, int(clock["origin_qpc"]), int(clock["frequency"])

    def build(self, outlines: list[dict], frames: list[int]) -> tuple[SceneSound | None, str | None]:
        if self.frequency != self.live.frequency:
            return None, "This scene's pictures and the recorder's sound count time on different clocks."
        presents = [(outlines[index]["present"], self.origin + outlines[index]["present_us"] * self.frequency
                     // 1_000_000) for index in frames
                    if outlines[index]["present"] is not None and outlines[index]["present_us"] is not None]
        if not presents:
            return None, NO_SOUND
        import live_audio
        chosen = self.live.span(presents[0][1], presents[-1][1])
        if not any(data is not None for _chunk, data in chosen):
            return None, self.live.silence(presents[0][1], presents[-1][1])
        payload, reason = live_audio.span_layer(chosen, presents, self.frequency, codec="stored")
        if payload is None:
            return None, reason
        layer = al.read_layer(payload)
        return SceneSound.of(type("Holder", (), {"audio": layer})(), [outlines[index]["present"] for index in frames]), None


def lost_frames(samples: list[int], losses: list[tuple[int, int]]) -> list[int]:
    """Frames whose sound, from their sample to the next frame's, overlaps a marked loss."""
    starts = [first for first, _count in losses]
    lost = []
    for frame, sample in enumerate(samples):
        if sample < 0:
            continue
        following = samples[frame + 1] if frame + 1 < len(samples) else -1
        end = following if following > sample else sample + 1
        at = bisect.bisect_right(starts, end - 1) - 1      # the last loss starting before the span ends
        if at >= 0 and starts[at] + losses[at][1] > sample:
            lost.append(frame)
    return lost
