"""THE PLUGIN VIDEO SOURCE: pictures from the capture layer, each already
stamped with the game's own memory (round 32 item 95).

The recorder's other sources photograph the desktop and the recorder then
asks the frame clock which game frame was current at that instant -- an
inference every reader since 2026-08-21 has been repairing. This source
reads the frame stream (`replay/framestream.py`) the capture layer writes
from inside Project64: one slot per presented picture, and beside its
pixels the bytes the layer copied out of RDRAM at the moment the game
submitted that picture's display list. Decoding those bytes with the
sampler's own decoder (`inputs/frame.py::decode`, over a reader that
un-swaps PJ64's words exactly as `memory/base.py` does) gives the picture's
frame, pad, Mario and IGT -- one definition of a pad for the track and for
the stamp.

The address table is written HERE, from the live layout: entry order is
`TABLE_ORDER`, every entry word-aligned so a halfword (`usamune_overall`)
sits inside a copied word. A layout with no controller address captures
the counter alone; no entry is ever invented.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from sm64_events.core.profiling import measured, profile
from sm64_events.inputs.frame import MARIO_BLOCK_OFF, MARIO_BLOCK_SIZE, InputFrame, decode
from sm64_events.memory import addresses as A
from sm64_events.memory.addresses import KSEG0_BASE
from sm64_events.memory.base import RdramReader
from sm64_events.replay import framestream as F
from sm64_events.replay.clock import _FREQ as QPC_FREQUENCY
from sm64_events.replay.pixels import BgrPicture

log = logging.getLogger("sm64.replay")

#: the table's entry order; the frame counter is entry 0 by contract
TABLE_ORDER = ("global_timer", "player1_controller", "mario", "usamune_overall")
#: how often the source re-reads the recorder's idle flag and the plugin's heartbeat
IDLE_POLL_S = 1.0
#: how long the reader waits on the event before checking stop / idle
WAIT_S = 0.25
#: how long the recorder waits for the layer's FIRST picture before it
#: records through desktop capture instead (a game presents ~30 pictures/s,
#: so a layer that can read at all answers inside a few of these)
PICTURE_PROBE_S = 0.6
#: how often the desktop camera looks for the layer's heartbeat
LAYER_WATCH_S = 1.0
#: after the layer refused pictures, how long before it is asked again
LAYER_RETRY_S = 15.0


def _refresh_profile(stream: F.FrameStream, stop_event: threading.Event) -> None:
    """Profiling must never make an otherwise working capture source fail."""
    try:
        stream.graphics_profile.refresh(
            profile.session_id if profile.active() else None, stop_event)
    except (OSError, ValueError):
        log.debug("graphics profiling mapping unavailable", exc_info=True)


def pictures_flow(stream: F.FrameStream, timeout_s: float = PICTURE_PROBE_S,
                  stop_event: threading.Event | None = None) -> tuple:
    """Whether the capture layer can hand over a picture RIGHT NOW: asks for
    frames and waits for its write sequence to advance. `(True, None)` on
    the first picture; `(False, reason)` otherwise, frames turned back off,
    with the reason the header gives -- the first live session (2026-09-05)
    had a layer whose heartbeat moved while every picture was refused (no
    GL context on the emulation thread), and the recorder sat on it for an
    hour recording nothing. A source that cannot deliver is never chosen;
    the desktop grab is, with this reason beside it."""
    import time
    before = stream.header()
    try:
        if stop_event is not None and stop_event.is_set():
            return False, "the picture probe was stopped"
        stream.touch()
        stream.set_want_frames(True)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                return False, "the picture probe was stopped"
            stream.touch()
            stream.wait(0.05)
            if stream.header().write_seq != before.write_seq:
                return True, None
        after = stream.header()
    finally:
        # A probe owns demand only while probing. The selected source takes
        # over in start(), including after a successful probe.
        stream.set_want_frames(False)
    if not after.initiated:
        return False, "the capture layer is not initiated"
    if after.dropped > before.dropped:
        return False, (f"it refused {after.dropped - before.dropped} pictures in "
                       f"{timeout_s:.1f} s: no OpenGL context on the emulation thread and "
                       "nothing from the wrapped plugin's ReadScreen")
    return False, f"it presented no new picture in {timeout_s:.1f} s"


def _aligned(address: int, length: int) -> tuple:
    """(rdram_offset, length) covering [address, address + length) on word
    boundaries -- what the plugin copies, and what `RegionMemory` can
    un-swap."""
    start = (address - KSEG0_BASE) & ~3
    end = (address - KSEG0_BASE + length + 3) & ~3
    return start, end - start


def table_for(layout) -> list:
    """[(name, rdram_offset, length)] for the rows this layout knows."""
    entries = []
    if layout.global_timer is not None:
        entries.append(("global_timer", *_aligned(layout.global_timer, 4)))
    if layout.player1_controller is not None:
        entries.append(("player1_controller",
                        *_aligned(layout.player1_controller, A.CONTROLLER_SIZE)))
    if layout.mario_struct is not None:
        entries.append(("mario", *_aligned(layout.mario_struct + MARIO_BLOCK_OFF,
                                           MARIO_BLOCK_SIZE)))
    if layout.usamune_overall is not None:
        entries.append(("usamune_overall", *_aligned(layout.usamune_overall, 2)))
    return entries


class RegionMemory(RdramReader):
    """An N64Memory over the few RDRAM regions a slot carries, in PJ64's own
    storage order -- so `read_u32` / `read_u16` / `read_block` decode them
    with the same swap rules the live reader uses."""

    def __init__(self, regions: list):
        # [(rdram_offset, raw bytes)]
        self._regions = [(offset, raw) for offset, raw in regions if raw]

    def _read_raw(self, offset: int, size: int) -> bytes:
        for start, raw in self._regions:
            if start <= offset and offset + size <= start + len(raw):
                return raw[offset - start:offset - start + size]
        raise KeyError(f"no region covers RDRAM {offset:#x}+{size}")


@dataclass(frozen=True)
class FrameStamp:
    frame: int
    igt_overall: int | None
    pad: InputFrame | None
    vi_origin: int
    list_qpc: int
    present_qpc: int
    lists_since: int

    def extras(self) -> dict:
        """The ledger row's fields (JSON-able)."""
        # ONE display list between this present and the last is the case
        # the stamp is exact for: the picture is that list's. Zero (the same
        # buffer presented again) or two-plus (a second list before the VI)
        # leaves which list the pixels came from an inference, and the row
        # says so instead of claiming exactness (review finding 9).
        out = {"exact": self.lists_since == 1, "vi_origin": self.vi_origin,
               "lists_since": self.lists_since}
        if self.igt_overall is not None:
            out["igt_overall"] = self.igt_overall
        if self.pad is not None:
            out["pad"] = [self.pad.stick_x, self.pad.stick_y, self.pad.buttons]
            out["mario"] = [self.pad.action, self.pad.yaw, round(self.pad.speed, 3)]
        return out


@measured("capture.decode_stamp")
def decode_stamp(slot: F.Slot, table: list, layout) -> FrameStamp | None:
    """The stamp a slot carries, decoded; None when the counter is missing."""
    by_name = {}
    regions = []
    for index, (name, offset, length) in enumerate(table):
        raw = slot.table[index] if index < len(slot.table) else b""
        if len(raw) == length:
            by_name[name] = raw
            regions.append((offset, raw))
    if "global_timer" not in by_name:
        return None
    memory = RegionMemory(regions)
    frame = memory.read_u32(layout.global_timer)
    igt = (memory.read_u16(layout.usamune_overall)
           if "usamune_overall" in by_name else None)
    pad = None
    if "player1_controller" in by_name:
        block = memory.read_block(layout.player1_controller, A.CONTROLLER_SIZE)
        mario = (memory.read_block(layout.mario_struct + MARIO_BLOCK_OFF, MARIO_BLOCK_SIZE)
                 if "mario" in by_name else None)
        pad = decode(block, mario)
    return FrameStamp(frame=frame, igt_overall=igt, pad=pad, vi_origin=slot.vi_origin,
                      list_qpc=slot.list_qpc, present_qpc=slot.present_qpc,
                      lists_since=slot.lists_since)


class DesktopUntilLayerPresents:
    """The camera the recorder gets while the capture layer is installed but
    not presenting: the desktop grab, with a watch on the frame stream.

    The moment the layer's heartbeat moves and a picture flows, this source
    ENDS ITSELF the way a lost window does -- `on_stopped` -- so the
    recorder's attach loop runs its factory again and gets the plugin
    source. Whichever order he opened the game and the trainer in, and
    whenever the ROM loads (his rule, 2026-09-05: "we need to be order
    agnostic"). His first restart attached to Project64's window two seconds
    before the ROM ran; the camera chosen then was never revisited, and the
    layer sat presenting to nobody.

    A layer that presents but refuses pictures is asked again every
    LAYER_RETRY_S, the reason on `frame_source_note` meanwhile."""

    frame_source = "desktop"

    def __init__(self, desktop, stream: F.FrameStream, note: str | None = None):
        self._desktop = desktop
        self._stream = stream
        self.frame_source_note = note
        self.upgraded = False
        self._on_stopped = None
        self._stop = threading.Event()
        self._thread = None

    def set_idle_check(self, fn) -> None:
        if hasattr(self._desktop, "set_idle_check"):
            self._desktop.set_idle_check(fn)

    def status(self) -> dict | None:
        result = self._desktop.status() if hasattr(self._desktop, "status") else None
        _refresh_profile(self._stream, self._stop)
        measured = self._stream.graphics_profile.snapshot(self._stream.header().plugin_pid)
        return {**(result or {}), "graphics_profile": measured}

    def start(self, on_frame, on_stopped) -> None:
        self._on_stopped = on_stopped
        if self._stop.is_set():
            return
        try:
            self._desktop.start(on_frame, on_stopped)
            self._thread = threading.Thread(target=self._watch, name="layer-watch", daemon=True)
            self._thread.start()
        except BaseException:
            self.stop()
            raise

    def request_stop(self) -> None:
        self._stop.set()
        _refresh_profile(self._stream, self._stop)
        try:
            self._stream.set_want_frames(False)
        except Exception:
            log.debug("frame stream gone at stop", exc_info=True)

    def stop(self) -> None:
        self.request_stop()
        thread = self._thread
        if (thread is not None and thread is not threading.current_thread()
                and thread.ident is not None):
            thread.join(timeout=2.0)
        self._thread = None
        try:
            # whatever a probe left on; the plugin source sets it again
            self._stream.set_want_frames(False)
        except Exception:
            log.debug("frame stream gone at stop", exc_info=True)
        self._desktop.stop()

    def _watch(self) -> None:
        import time
        next_probe = 0.0
        last = self._stream.header().alive
        while not self._stop.wait(LAYER_WATCH_S):
            _refresh_profile(self._stream, self._stop)
            try:
                header = self._stream.header()
            except Exception:
                log.debug("frame stream unreadable in the layer watch", exc_info=True)
                continue
            alive = header.alive
            moving = header.initiated and alive != last
            last = alive
            if not moving or time.monotonic() < next_probe:
                continue
            try:
                flowing, reason = pictures_flow(self._stream, stop_event=self._stop)
            except Exception:
                log.exception("capture layer probe failed; staying on desktop capture")
                next_probe = time.monotonic() + LAYER_RETRY_S
                continue
            if self._stop.is_set():
                return
            if flowing:
                self.upgraded = True
                log.info("capture layer started presenting; handing the recorder over to it")
                if self._on_stopped is not None:
                    self._on_stopped()
                return
            self.frame_source_note = f"the capture layer is loaded but {reason}"
            log.warning("%s; staying on desktop capture, asking again in %.0f s",
                        self.frame_source_note, LAYER_RETRY_S)
            next_probe = time.monotonic() + LAYER_RETRY_S


class PluginVideoSource:
    """The recorder's `VideoSource` over the frame stream.

    `on_frame(pixels, ts_100ns, stamp)`: an owned BgrPicture, its present
    time in WGC's timebase (QPC in 100 ns -- the same clock the DWM source
    stamps with, so the CaptureClock needs no second anchor), and the
    decoded `FrameStamp`. Frames are asked for while the recorder is not
    idle; the reader thread wakes on the plugin's event."""

    frame_source = "plugin"

    def __init__(self, stream: F.FrameStream, table: list, layout, fps: int = 60):
        self._stream = stream
        self._table = table
        self._layout = layout
        self._fps = fps
        self._thread = None
        self._stop = threading.Event()
        self._idle_check = lambda: False
        self._last_seq = stream.header().write_seq
        self._demand_lock = threading.Lock()
        self._accept_demand = False
        self._skipped = 0
        self._undecodable = 0
        self._delivered = 0

    def set_idle_check(self, fn) -> None:
        self._idle_check = fn

    def refresh_demand(self) -> None:
        """Apply the recorder's current idle state without waiting for a VI.

        The stopped source never enables capture again, including a resume
        notification racing its teardown.
        """
        with self._demand_lock:
            if self._accept_demand:
                self._stream.set_want_frames(not self._idle_check())

    def start(self, on_frame, on_stopped) -> None:
        try:
            with self._demand_lock:
                if self._thread is not None or self._stop.is_set():
                    return
                self._stream.touch()
                self._accept_demand = True
                self._stream.set_want_frames(not self._idle_check())
                self._thread = threading.Thread(target=self._loop, args=(on_frame, on_stopped),
                                                name="plugin-frames", daemon=True)
                self._thread.start()
        except BaseException:
            self.request_stop()
            self._thread = None
            raise

    def request_stop(self) -> None:
        """Revoke capture immediately, before any joins or encoder draining."""
        self._stop.set()
        _refresh_profile(self._stream, self._stop)
        try:
            with self._demand_lock:
                self._accept_demand = False
                self._stream.set_want_frames(False)
        except Exception:
            log.debug("frame stream gone at stop", exc_info=True)

    def stop(self) -> None:
        self.request_stop()
        if self._thread is not None:
            if self._thread is not threading.current_thread():
                self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self, on_frame, on_stopped) -> None:
        try:
            self._read_loop(on_frame)
        except Exception:
            log.exception("plugin frame reader failed; source ends")
        finally:
            self.request_stop()
            try:
                on_stopped()
            except Exception:
                log.exception("plugin source on_stopped failed")

    def _read_loop(self, on_frame) -> None:
        import time
        last_alive = self._stream.header().alive
        last_alive_check = time.monotonic()
        while not self._stop.is_set():
            self._stream.wait(WAIT_S)
            if self._stop.is_set():
                break
            slots, skipped = self._stream.read_new(self._last_seq)
            self._skipped += skipped
            for slot in slots:
                self._last_seq = slot.seq
                try:
                    stamp = decode_stamp(slot, self._table, self._layout)
                except Exception:
                    # A slot whose bytes do not decode (a table the layout
                    # disagrees with) is counted, never fatal to the source.
                    log.exception("plugin stamp did not decode; slot dropped")
                    stamp = None
                if stamp is None:
                    self._undecodable += 1
                    continue
                ts_100ns = slot.present_qpc * 10_000_000 // QPC_FREQUENCY
                try:
                    on_frame(BgrPicture(slot.pixels), ts_100ns, stamp)
                    self._delivered += 1
                except Exception:
                    log.exception("plugin frame callback failed; frame dropped")
            now = time.monotonic()
            if now - last_alive_check >= IDLE_POLL_S:
                last_alive_check = now
                _refresh_profile(self._stream, self._stop)
                with self._demand_lock:
                    if self._accept_demand:
                        self._stream.touch()
                alive = self._stream.header().alive
                if alive == last_alive and (self._stream.header().initiated is False
                                            or not self._stream.plugin_process_alive()):
                    # The plugin closed (ROM closed, PJ64 exited) or its
                    # process died with the header's bits still set: hand
                    # the recorder back to its attach loop, like a lost
                    # window.
                    log.info("capture layer stopped presenting; source ends")
                    break
                last_alive = alive

    def status(self) -> dict:
        _refresh_profile(self._stream, self._stop)
        header = self._stream.header()
        return {"delivered": self._delivered, "skipped": self._skipped,
                "undecodable": self._undecodable,
                "dropped_by_plugin": header.dropped,
                "graphics_profile": self._stream.graphics_profile.snapshot(header.plugin_pid)}
