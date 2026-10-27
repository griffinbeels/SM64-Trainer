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
lost, stems}: `samples` per frame (-1 where the frame's picture has no sound: before or after the
recording, or in a gap), `lost` the frames whose sound span (to the next frame's sample) touches
a marked loss. The stored stream holds silence there, so it plays as silence, and the page says
so. A block is served whole, as the .flac file the layer's block makes with its STREAMINFO (or a
.wav for a stored layer), once its decoded PCM has matched its hash; a block that does not is
refused, never served.

`stems` is the scene's music and sound effects (round 4, item 11: "toggle music / game sound
effects separately"): {available: true, tracks, lost} when the package carries a source-stems
layer that split it (stems_layer.py), else {available: false, reason} (a refused split: reason
NOT_SPLIT, plus the layer's own "detail" and "stats", also logged when the scene opens). The two tracks lie on the
recording's own samples and blocks, so a frame's sample and a block's index mean the same in all
three: block(index, "music") is the same span as block(index). The viewer plays the recording
while both are on, and one track while only that one is; `lost` names the frames whose span
touches a stretch with no split, where a single track plays silence.
"""
from __future__ import annotations

import bisect
import io
import logging
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio_clock as ac  # noqa: E402
import audio_layer as al  # noqa: E402

log = logging.getLogger("sm64.scene_lab")
SOUND_SHIFT_MS = 0.0          # 0: as heard. Project64's audio delay here: each sound with its game event
NO_SOUND = "This recording has no sound."
NO_STEMS = "This scene was saved before music and sound effects could be split."
NOT_SPLIT = "This scene's music and sound effects can't be separated."
STEMS_PENDING = "Music and sound effects can be switched a few seconds after the attempt ends."
TRACKS = ("mix", "music", "effects")
LIVE_BLOCK_SAMPLES = al.FLAC_FRAME * 20     # audio_layer.encode_layer's blocks, so the attached layer's match
PENDING = -2                  # LiveSound: a frame whose sound has not reached the ring yet


def absent() -> dict:
    """describe() for a package without a source-audio layer."""
    return {"available": False, "reason": NO_SOUND}


class SceneSound:
    """One package's sound, mapped to the viewer's frames."""

    def __init__(self, layer: al.Layer, samples: list[int], lost: list[int], shift_ms: float, stems=None):
        self.layer, self.samples, self.lost, self.shift_ms = layer, samples, lost, shift_ms
        self.stems = stems                        # stems_layer.Stems, or None when the package has none
        self.checked: set[tuple[str, int]] = set()   # (track, block) whose PCM matched its hash

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
        stems = getattr(package, "stems", None)
        if stems is not None and not stems.available:
            log.info("scene sound: music and sound effects not split: %s %s", stems.reason, stems.stats)
        return cls(layer, samples, lost_frames(samples, layer.losses), shift_ms, stems)

    def describe(self) -> dict:
        layer = self.layer
        return {"available": True, "rate": layer.rate, "block_samples": layer.block_samples,
                "total_samples": layer.total_samples, "blocks": len(layer.blocks),
                "codec": "flac" if layer.codec == al.FLAC else "stored", "shift_ms": self.shift_ms,
                "samples": self.samples, "lost": self.lost, "stems": self._stems()}

    def _stems(self) -> dict:
        """The split as the page shows it. A refused split says NOT_SPLIT; why, in the layer's own
        words and numbers, goes to "detail" (the page's ui_log note) and the server log."""
        stems = self.stems
        if stems is None:
            return {"available": False, "reason": NO_STEMS}
        if not stems.available:
            return {"available": False, "reason": NOT_SPLIT, "detail": stems.reason, "stats": stems.stats}
        return {"available": True, "tracks": list(TRACKS[1:]), "lost": lost_frames(self.samples, stems.gaps)}

    def block(self, index: int, track: str = "mix") -> tuple[bytes, str]:
        """Block `index` of a track ("mix": the recording; "music", "effects": its stems) as a
        file the browser decodes: LookupError for no such block or track, a ValueError
        (LayerError, StemsError) when its decoded PCM misses its hash. Thread-safe: the layers
        are immutable."""
        layer, stems = self.layer, self.stems
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(layer.blocks):
            raise LookupError("This recording's sound has no such block.")
        if track != "mix" and (track not in TRACKS or stems is None or not stems.available):
            raise LookupError("This recording's sound has no such track.")
        if (track, index) not in self.checked:
            if track == "mix":
                layer.block(index)                               # decodes and checks the hash
            else:
                stems.block(track, index)
            self.checked.add((track, index))
        if track != "mix":
            return al.flac_file(stems.stored(track, index), stems.rate), "audio/flac"
        offset, stored, _samples, _digest = layer.blocks[index]
        raw = layer.payload[offset:offset + stored]
        if layer.codec == al.FLAC:
            return al.flac_file(raw, layer.rate), "audio/flac"
        return _wav(raw, layer.rate), "audio/wav"


def _wav(pcm: bytes, rate: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as file:
        file.setnchannels(2)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(pcm)
    return out.getvalue()


class LiveSound:
    """A scene's sound from the recorder's own ring (live_audio.LiveAudio) while its package has
    no layer yet: an attempt scene still growing, or one whose sound the cutter is still adding.
    So the scene plays with sound from its first picture and does not switch mid-play.

    The audio clock is fit once (request, then adopt): at open, the ring's chunks around the
    frames so far go to the cutter child as stamps (AttemptScenes.fit), and the child fits them
    (live_audio.fit_stamps). It runs there because the server's poll loop shares this process's
    lock: measured 2026-10-26 with a full 300 s ring beside a 4 ms ticker thread, fitting a
    120 s attempt in the server took 38 ms and stretched a tick to 17 ms; handing its stamps
    over (request) takes 10 ms and stretches none past the ticker's own 5 ms (30 s: 3 ms;
    280 s: 23 ms and a 9 ms tick). The child fit his 5 s BoB attempt in 14 ms beside its take.
    Frames newer than the fit take its last line, extrapolated: the two clocks drift about
    150 ppm apart, so a frame 5 s past the fit is off by under 1 ms. The ring's newer chunks
    extend the stream as they arrive (extend). The stream starts where the attached layer's
    will (live_audio.write_span's first chunk) and only grows, in the layer's block size, so a
    block the page holds stays valid when the attached layer takes over (SceneSession).

    `clock` is the package index's "clock" (the QPC its present times count from, and the QPC's
    frequency); `fitter` is AttemptScenes.fit. describe() and block() are SceneSound's, with
    "live": true. Not covered until the attached layer takes over: a hole or overrun in the
    ring after the fit (its frames play shifted by it), and a new tap epoch after the fit (its
    frames stay silent)."""

    def __init__(self, live, clock: dict, fitter, shift_ms: float = SOUND_SHIFT_MS):
        import live_audio

        self.live, self.fitter, self.shift_ms, self.rate = live, fitter, shift_ms, live_audio.RATE
        self.origin, self.frequency = int(clock["origin_qpc"]), int(clock["frequency"])
        self.reason: str | None = None            # why there is no sound (yet)
        self.future = None                        # the child's fit, until adopted
        self.requested = 0.0
        self.base: int | None = None              # the first picture's present: the fit's time origin
        self.segments: list[ac.Segment] | None = None
        self.losses: list[tuple[int, int]] = []
        self.pcm = bytearray()                    # the stored stream, s16 stereo; only ever appended to
        self.tail: tuple[int, int] | None = None  # (epoch, next sample) the stream continues from
        self.samples: list[int] = []
        self.lost: list[int] = []
        self.waiting: int | None = None           # the first frame whose sound has not arrived yet
        self.fit_ms: float | None = None

    @property
    def available(self) -> bool:
        return self.segments is not None and self.reason is None

    def _qpcs(self, outlines: list[dict], frames: list[int]) -> list[int | None]:
        return [None if outlines[index]["present_us"] is None
                else self.origin + outlines[index]["present_us"] * self.frequency // 1_000_000 for index in frames]

    def request(self, outlines: list[dict], frames: list[int]) -> None:
        """At open: takes the ring's chunks around the frames so far and asks for the fit."""
        if self.frequency != self.live.frequency:
            self.reason = "This scene's pictures and the recorder's sound count time on different clocks."
            return
        qpcs = [qpc for qpc in self._qpcs(outlines, frames) if qpc is not None]
        if not qpcs:
            self.reason = NO_SOUND
            return
        records = self.live.records(qpcs[0], qpcs[-1])
        if not any(record[5] is not None for record in records):
            self.reason = self.live.silence(qpcs[0], qpcs[-1])
            return
        import live_audio

        self.base, self.requested = qpcs[0], time.monotonic()
        self.pcm = bytearray(live_audio.pcm_of(records))
        self.tail = (records[-1][0], records[-1][1] + records[-1][2])
        self.reason = "This scene's sound is still being lined up with its pictures."
        self.future = self.fitter(live_audio.stamps_of(records), self.base, self.frequency)

    def adopt(self, wait: float = 0.0) -> bool:
        """Takes the child's fit once it has it (waiting up to `wait` s); True when it did."""
        if self.future is None:
            return False
        try:
            fit = self.future.result(timeout=wait)
        except TimeoutError:
            return False
        self.future = None
        self.fit_ms = fit.get("fit_ms")
        if fit.get("reason") or fit.get("total") != len(self.pcm) // 4:
            self.reason = fit.get("reason") or "This scene's sound did not line up with what the ring held."
            self.pcm, self.tail = bytearray(), None
            return True
        self.segments = [ac.Segment(*row) for row in fit["segments"]]
        self.losses = [tuple(loss) for loss in fit["losses"]]
        self.reason = None
        return True

    def extend(self, outlines: list[dict], frames: list[int], start: int, whole: bool) -> bool:
        """Appends the ring's newer chunks and maps frames `start` on (and any still waiting for
        their sound); `whole`: the scene has all its frames. True when the table changed."""
        if not self.available:
            return False
        qpcs = self._qpcs(outlines, frames)
        newest = next((qpc for qpc in reversed(qpcs) if qpc is not None), self.base)
        grew = False
        if self.tail is not None:
            records, state = self.live.after(*self.tail, newest)
            for record in records:
                if record[5] is None:
                    self._lose(len(self.pcm) // 4, record[2])
                    self.pcm += bytes(record[2] * 4)
                else:
                    self.pcm += record[5]
            if records:
                self.tail, grew = (records[-1][0], records[-1][1] + records[-1][2]), True
            if state == "broken" or state == "end" and whole:
                self.tail = None                 # the stream is as long as it will be
        begin = min(start, len(self.samples) if self.waiting is None else self.waiting)
        if begin >= len(frames) and not grew:
            return False
        total = len(self.pcm) // 4
        self.samples = self.samples[:begin]
        self.waiting = None
        for position in range(begin, len(frames)):
            sample = self._sample(qpcs[position], total)
            if sample == PENDING:
                self.waiting = position if self.waiting is None else self.waiting
                sample = -1
            self.samples.append(sample)
        if self.tail is None:
            self.waiting = None                  # nothing more will arrive for them
        again = max(0, begin - 1)                # a frame's loss reaches to the next frame's sample
        self.lost = [frame for frame in self.lost if frame < again] + [
            again + frame for frame in lost_frames(self.samples[again:], self.losses)]
        return True

    def _sample(self, qpc: int | None, total: int) -> int:
        if qpc is None:
            return -1
        ticks = qpc - self.base + round(self.shift_ms * self.frequency / 1000)
        status, sample = ac.locate(ticks, self.segments, self.losses)
        if status == ac.AFTER:                   # past the fit: its last line, extrapolated
            last = self.segments[-1]
            sample = last.stream_first + (((ticks - last.anchor) << ac.Q) // last.period_q32)
            return sample if sample < total else PENDING
        return sample if status in (ac.COVERED, ac.LOSS) else -1

    def _lose(self, first: int, samples: int) -> None:
        if self.losses and self.losses[-1][0] + self.losses[-1][1] == first:
            self.losses[-1] = (self.losses[-1][0], self.losses[-1][1] + samples)
        else:
            self.losses.append((first, samples))

    def describe(self) -> dict:
        total = len(self.pcm) // 4
        return {"available": True, "rate": self.rate, "block_samples": LIVE_BLOCK_SAMPLES,
                "total_samples": total, "blocks": -(-total // LIVE_BLOCK_SAMPLES), "codec": "stored",
                "shift_ms": self.shift_ms, "samples": self.samples, "lost": self.lost, "live": True,
                "stems": {"available": False, "reason": STEMS_PENDING}}

    def block(self, index: int, track: str = "mix") -> tuple[bytes, str]:
        """Block `index` of the stream so far, as a .wav file. The stream is only appended to, so
        a block read while it grows is the same block later (the last may be shorter). The ring
        holds the recording only: its stems come with the layer the cutter adds."""
        if track != "mix":
            raise LookupError("This recording's sound has no such track.")
        total = len(self.pcm) // 4
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < -(-total // LIVE_BLOCK_SAMPLES):
            raise LookupError("This recording's sound has no such block.")
        first = index * LIVE_BLOCK_SAMPLES
        return _wav(bytes(self.pcm[first * 4:min(total, first + LIVE_BLOCK_SAMPLES) * 4]), self.rate), "audio/wav"


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
