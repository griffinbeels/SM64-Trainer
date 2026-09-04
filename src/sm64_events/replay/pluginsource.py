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

import numpy as np

from sm64_events.inputs.frame import MARIO_BLOCK_OFF, MARIO_BLOCK_SIZE, InputFrame, decode
from sm64_events.memory import addresses as A
from sm64_events.memory.addresses import KSEG0_BASE
from sm64_events.memory.base import RdramReader
from sm64_events.replay import framestream as F
from sm64_events.replay.clock import _FREQ as QPC_FREQUENCY

log = logging.getLogger("sm64.replay")

#: the table's entry order; the frame counter is entry 0 by contract
TABLE_ORDER = ("global_timer", "player1_controller", "mario", "usamune_overall")
#: how often the source re-reads the recorder's idle flag and the plugin's heartbeat
IDLE_POLL_S = 1.0
#: how long the reader waits on the event before checking stop / idle
WAIT_S = 0.25


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
        out = {"exact": True, "vi_origin": self.vi_origin,
               "lists_since": self.lists_since}
        if self.igt_overall is not None:
            out["igt_overall"] = self.igt_overall
        if self.pad is not None:
            out["pad"] = [self.pad.stick_x, self.pad.stick_y, self.pad.buttons]
            out["mario"] = [self.pad.action, self.pad.yaw, round(self.pad.speed, 3)]
        return out


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


def to_bgra_top_down(pixels_bgr_bottom_up: np.ndarray) -> np.ndarray:
    """The (H, W, 4) top-down array the sink expects, from a slot's rows."""
    height, width = pixels_bgr_bottom_up.shape[:2]
    out = np.empty((height, width, 4), dtype=np.uint8)
    out[:, :, :3] = pixels_bgr_bottom_up[::-1]
    out[:, :, 3] = 255
    return out


class PluginVideoSource:
    """The recorder's `VideoSource` over the frame stream.

    `on_frame(bgra, ts_100ns, stamp)`: the picture top-down BGRA, its present
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
        self._skipped = 0
        self._undecodable = 0
        self._delivered = 0

    def set_idle_check(self, fn) -> None:
        self._idle_check = fn

    def start(self, on_frame, on_stopped) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._stream.set_want_frames(not self._idle_check())
        self._thread = threading.Thread(target=self._loop, args=(on_frame, on_stopped),
                                        name="plugin-frames", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._stream.set_want_frames(False)
        except Exception:
            log.debug("frame stream gone at stop", exc_info=True)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self, on_frame, on_stopped) -> None:
        import time
        last_alive = self._stream.header().alive
        last_alive_check = time.monotonic()
        while not self._stop.is_set():
            self._stream.wait(WAIT_S)
            slots, skipped = self._stream.read_new(self._last_seq)
            self._skipped += skipped
            for slot in slots:
                self._last_seq = slot.seq
                stamp = decode_stamp(slot, self._table, self._layout)
                if stamp is None:
                    self._undecodable += 1
                    continue
                ts_100ns = slot.present_qpc * 10_000_000 // QPC_FREQUENCY
                try:
                    on_frame(to_bgra_top_down(slot.pixels), ts_100ns, stamp)
                    self._delivered += 1
                except Exception:
                    log.exception("plugin frame callback failed; frame dropped")
            now = time.monotonic()
            if now - last_alive_check >= IDLE_POLL_S:
                last_alive_check = now
                self._stream.touch()
                self._stream.set_want_frames(not self._idle_check())
                alive = self._stream.header().alive
                if alive == last_alive and self._stream.header().initiated is False:
                    # The plugin closed (ROM closed / PJ64 exited): hand the
                    # recorder back to its attach loop, like a lost window.
                    log.info("capture layer stopped presenting; source ends")
                    break
                last_alive = alive
        try:
            on_stopped()
        except Exception:
            log.exception("plugin source on_stopped failed")

    def status(self) -> dict:
        return {"delivered": self._delivered, "skipped": self._skipped,
                "undecodable": self._undecodable,
                "dropped_by_plugin": self._stream.header().dropped}
