"""The sound of live play for the scenes the lab cuts from it (M4 E4-audio, lab only).

    live = LiveAudio()                                  # once, before the recorder is built
    recorder = ReplayRecorder(..., audio_factory=live.wrap(primary), fallback_audio_factory=live.wrap(fallback))
    live.write_span(qpc_from, qpc_to, capture)          # an attempt's sound, in audio_session.py's capture format
    attach_span(package, capture, presents, frequency, rom)   # None, or why the scene stays silent

Intent: a scene cut from an attempt Griffin just played opens with the sound the game made
during it ("is it possible that we could playback the exact sound that played?", round 1).
In live play the Trainer's recorder owns Project64's audio tap, and a second tap would be a
second, competing recorder, so the lab takes a copy of the recorder's own PCM instead:
`wrap` puts a bounded fanout between the recorder's audio source and its callback.

The fanout (audio_capture.Fanout in ring mode, the lab capture's own) runs on the source's
delivery thread. Per chunk it stamps the arrival with QPC (time.perf_counter_ns() as ticks:
the counter the scene tap stamps presents with, so the two join with no anchor), hands the
recorder the very same array, unchanged and first, then appends a copy to a ring that keeps
the last `seconds` (and at most `budget_bytes` of PCM), dropping the oldest. It takes no
lock and never waits; a failure of its own is counted and never reaches the recorder. Each
start of a source is a new tap epoch, its samples counting from 0, so a restart splits the
audio clock instead of bending it. `write_span` copies the ring in one step and writes the
chunks that arrived from `margin_s` before an attempt's first picture to as long after its
last; `attach_span` fits the audio clock to the package's own presents (audio_clock.py),
encodes the layer (audio_layer.py) and writes the package again in place with it.

Isolation: only Project64's own process tap (replay/audio.py ProcessAudioSource, mode
"process") feeds the ring. When the recorder runs its device-loopback fallback, which records
everything on that output, the PCM passes straight to the recorder, the ring keeps none of it,
health() says so, and a span recorded that way is refused with the reason.

Evidence (2026-10-26, synthetic): the recorder receives the same array objects it receives
unwrapped; a cut scene's frames map within 1 ms of their true samples and its clicks land on
their pictures through the viewer's own session. Per 10 ms chunk the wrapper costs the pump's
thread 1.3 us p50 / 2.1 us p99 with the ring full and evicting (1.1 / 3.8 us while it fills,
one 4.7 ms outlier as its memory grew); a full 900 s ring is 208 MB of private memory (173 MB
of it PCM) and adds 1.7 ms to a full garbage collection. Not yet seen live: a real proctap
stream through this path.

Limits: the recorder starts its audio source only while replay recording is on (its "media"
profile; ghosts-only and live-ghost profiles start none), so with recording off no scene has
sound. Packets the source's own pump drops before the recorder's callback never reach the
ring: the clock fit shows them as holes, and those pictures play silent. The ring holds raw
PCM, 192 KB a second, so `seconds` sets the server's memory (above). It holds private game
audio in memory only; the files it writes belong outside the repository.
"""
from __future__ import annotations

import bisect
import os
import sys
import threading
from collections import deque
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio_capture as cap  # noqa: E402
import audio_clock as ac  # noqa: E402
import audio_layer as al  # noqa: E402
import audio_session as asn  # noqa: E402

RATE = cap.RATE
MARGIN_S = 2.0                # audio kept on each side of a span, so the clock fit sees past its edges
LATE_S = 0.5                  # a chunk can arrive this long after its last sample sounded
STALE_S = 2.0                 # the process tap delivers silence as zeros, so a quiet ring means trouble
SOURCES_KEPT = 64

NOT_STARTED = ("The Trainer has not recorded Project64's sound yet: it records only while replay "
               "recording is on and Project64 is open.")
NOT_ISOLATED = ("Project64's own sound could not be isolated, so the recorder fell back to the whole "
                "output device; scenes stay silent rather than carry other programs' sound.")


class _Tapped:
    """The recorder's audio source as it was, its PCM also copied into the ring.

    Everything but start and stop passes through to the source (mode, check_health,
    ghost_timeline, ...), so the recorder cannot tell the two apart."""

    def __init__(self, source, live: LiveAudio):
        self._source, self._live = source, live
        self.token = -1                    # the fanout epoch this source feeds; any other is stale
        self._entry: list | None = None

    @property
    def mode(self):
        return getattr(self._source, "mode", None)

    def __getattr__(self, name):
        if name == "_source":              # not yet set: never recurse
            raise AttributeError(name)
        return getattr(self._source, name)

    def start(self, on_pcm) -> None:
        live = self._live
        self._entry = live._started(self)
        if self.mode != "process":
            self._source.start(on_pcm)     # device loopback: the ring keeps none of it
            return
        stamp, feed = live._stamp, live._feed

        def both(pcm) -> None:
            arrival = stamp()
            try:
                on_pcm(pcm)                # the recorder first, with the very same array
            finally:
                feed(self, pcm, arrival)

        self._source.start(both)

    def stop(self) -> None:
        try:
            self._source.stop()
        finally:
            self._live._stopped(self._entry)


class LiveAudio:
    """The lab's bounded copy of the recorder's game sound: one ring, fed by whichever wrapped
    source the recorder is running (it runs one at a time)."""

    def __init__(self, *, seconds: float = 900.0, budget_bytes: int | None = None,
                 frequency: int | None = None, stamp=None) -> None:
        if not 1.0 <= seconds <= 4 * 3600:
            raise ValueError("the live sound buffer keeps between 1 s and 4 h")
        self.seconds = seconds
        self.frequency = frequency or cap.qpc_frequency()
        self._stamp = stamp or cap.qpc_stamp(self.frequency)
        self.budget_bytes = budget_bytes if budget_bytes is not None else int(seconds * RATE * 4)
        self._fanout = cap.Fanout(self._stamp, self.budget_bytes, keep_ticks=round(seconds * self.frequency))
        self._sources: deque[list] = deque(maxlen=SOURCES_KEPT)   # [mode, epoch, started, stopped | None]
        self._lock = threading.Lock()      # start/stop bookkeeping only; never taken on the PCM path
        self.superseded = self.errors = 0

    def wrap(self, factory):
        """An audio factory (pid -> source) whose sources also feed this ring; None stays None."""
        if factory is None:
            return None
        return lambda pid: _Tapped(factory(pid), self)

    # -- the recorder's threads ----------------------------------------------------------

    def _started(self, tapped: _Tapped) -> list:
        with self._lock:
            self._fanout.begin_epoch()
            tapped.token = self._fanout.epoch
            entry = [tapped.mode, tapped.token, self._stamp(), None]
            self._sources.append(entry)
        return entry

    def _stopped(self, entry: list | None) -> None:
        if entry is not None and entry[3] is None:
            entry[3] = self._stamp()

    def _feed(self, tapped: _Tapped, pcm, arrival: int) -> None:
        """On the delivery thread, after the recorder had the chunk: never raises, never waits."""
        fanout = self._fanout
        try:
            if tapped.token != fanout.epoch:
                self.superseded += 1
                return
            fanout.on_pcm(pcm, arrival)
        except Exception:  # noqa: BLE001 - the lab's copy must never cost the recorder its audio
            self.errors += 1
            fanout.begin_epoch()           # the count may have moved without a chunk: restart it
            tapped.token = fanout.epoch

    # -- the lab's side --------------------------------------------------------------------

    def health(self) -> dict:
        """What the ring holds and whether it is taking the game's own sound right now."""
        fanout, now = self._fanout, self._stamp()
        records = fanout.snapshot()
        current = self._sources[-1] if self._sources else None
        newest = records[-1][3] if records else None
        running = current is not None and current[3] is None
        if current is None:
            reason = NOT_STARTED
        elif running and current[0] != "process":
            reason = NOT_ISOLATED
        elif not running:
            reason = "The recorder stopped recording sound (replay recording turned off, or Project64 closed)."
        elif newest is None or now - newest > STALE_S * self.frequency:
            reason = "Project64's sound stopped reaching the Trainer."
        else:
            reason = None
        kept = sum(record[2] for record in records if record[5] is not None)
        return {"recording": reason is None, "reason": reason, "mode": current[0] if current else None,
                "seconds": self.seconds, "kept_seconds": round(kept / RATE, 3),
                "kept_bytes": fanout.bytes_in - fanout.bytes_out, "budget_bytes": self.budget_bytes,
                "chunks": len(records), "epoch": fanout.epoch, "lost_records": fanout.lost_records,
                "lost_samples": fanout.lost_samples, "evicted_chunks": fanout.evicted_chunks,
                "superseded": self.superseded, "errors": self.errors, "chunk_cost_us": fanout.cost_us()}

    def write_span(self, qpc_from: int, qpc_to: int, out: Path, *, margin_s: float = MARGIN_S) -> dict:
        """The chunks around [qpc_from, qpc_to] (Project64's QPC ticks, e.g. an attempt's first
        and last present) written to `out` atomically in audio_session.py's capture format.
        {"written": bool, "reason": plain sentence or None, ...}: the counts, and whether the ring
        held sound from before the span's start and past its end ("covers_start", "covers_end")."""
        if type(qpc_from) is not int or type(qpc_to) is not int or qpc_to < qpc_from:
            return {"written": False, "reason": "This attempt has no span of pictures to find sound for."}
        margin = round(margin_s * self.frequency)
        records = self._fanout.snapshot()
        arrivals = [record[3] for record in records]
        start = bisect.bisect_left(arrivals, qpc_from - margin)
        stop = bisect.bisect_right(arrivals, qpc_to + margin + round(LATE_S * self.frequency))
        chosen = [(cap.chunk_of(record), record[5]) for record in records[start:stop]]
        if not any(data is not None for _chunk, data in chosen):
            return {"written": False, "reason": self._why_silent(qpc_from, qpc_to, records)}
        out = Path(out)
        partial = out.with_name(out.name + ".partial")
        asn.write_capture(partial, RATE, self.frequency, chosen)
        os.replace(partial, out)
        kept = [chunk for chunk, data in chosen if data is not None]
        return {"written": True, "reason": None, "chunks": len(chosen),
                "seconds": round(sum(chunk.samples for chunk, _data in chosen) / RATE, 3),
                "lost_samples": sum(chunk.samples for chunk, data in chosen if data is None),
                "epochs": len({chunk.epoch for chunk, _data in chosen}),
                "covers_start": kept[0].arrival - self._ticks(kept[0].samples) <= qpc_from,
                "covers_end": kept[-1].arrival >= qpc_to, "bytes": out.stat().st_size}

    def _ticks(self, samples: int) -> int:
        return samples * self.frequency // RATE

    def _why_silent(self, qpc_from: int, qpc_to: int, records: list) -> str:
        """One sentence: why the ring holds no sound for the span."""
        overlapping = [entry for entry in list(self._sources)
                       if entry[2] <= qpc_to and (entry[3] is None or entry[3] >= qpc_from)]
        if any(entry[0] != "process" for entry in overlapping):
            return NOT_ISOLATED            # the device loopback ran then, and the ring kept none of it
        if records and qpc_to < records[0][3]:
            keeps = f"{self.seconds / 60:g} minutes" if self.seconds >= 120 else f"{self.seconds:g} seconds"
            return f"This attempt's sound has already left the live sound buffer, which keeps the last {keeps}."
        if not self._sources:
            return NOT_STARTED
        if records and qpc_from > records[-1][3] and self._sources[-1][3] is None:
            return "This attempt's sound has not reached the Trainer yet."
        return "The Trainer was not recording Project64's sound during this attempt."


def attach_span(package: Path, capture: Path, presents: Sequence[tuple[int, int]], qpc_frequency: int,
                rom: bytes) -> str | None:
    """Adds the capture as the cut package's source-audio layer, in place (atomic replace).
    `presents`: (present sequence, absolute QPC) of the package's pictures. None on success,
    else one plain sentence saying why the scene stays silent (the package is left as it was)."""
    import package as scene_package

    try:
        rate, frequency, chunks, payloads = asn.read_capture(Path(capture))
    except (OSError, asn.CaptureError) as error:
        return f"This attempt's sound could not be read ({error})."
    if frequency != qpc_frequency:
        return "This attempt's sound was timed on a different clock than its pictures."
    pictures = [ac.Present(sequence, qpc)
                for sequence, qpc in sorted({(int(sequence), int(qpc)) for sequence, qpc in presents})]
    try:
        clock = ac.build_clock(chunks, pictures, rate=rate, frequency=frequency)
        pcm = ac.stream_pcm(chunks, payloads)
    except ac.ClockError as error:
        return f"This attempt's sound could not be lined up with its pictures ({error})."
    statuses = clock.stats["pictures"]
    if not statuses["covered"] + statuses["loss"]:
        return "No game sound was recorded while this attempt's pictures were on screen."
    try:
        payload = al.encode_layer(clock, pcm, codec="flac")
        data = scene_package.with_audio(Path(package).read_bytes(), rom, payload)
    except OSError as error:
        return f"The scene package could not be read ({error.strerror or error})."
    except ValueError as error:            # audio_layer.LayerError, package.PackageError
        return f"This attempt's sound could not be added to its scene ({error})."
    partial = Path(package).with_name(Path(package).name + ".partial")
    try:
        partial.write_bytes(data)
        os.replace(partial, package)
    except OSError as error:
        partial.unlink(missing_ok=True)
        return f"The scene package could not be written again with its sound ({error.strerror or error})."
    return None
