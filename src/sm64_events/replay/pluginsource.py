"""THE STAMP: the game's own memory beside every captured picture, and the
desktop camera that waits for the capture layer (round 32 item 95).

The capture layer copies the tracker's address table out of RDRAM at the
moment the game submits a picture's display list; the delivery worker hands
those bytes to Python beside the encoded picture. Decoding them with the
sampler's own decoder (`inputs/frame.py::decode`, over a reader that
un-swaps PJ64's words exactly as `memory/base.py` does) gives the picture's
frame, pad, Mario and IGT -- one definition of a pad for the track and for
the stamp. `replay/gpuinput.py` calls `decode_stamp` for every GPU picture.

The address table is written HERE, from the live layout: entry order is
`TABLE_ORDER`, every entry word-aligned so a halfword (`usamune_overall`)
sits inside a copied word. A layout with no controller address captures
the counter alone; no entry is ever invented.

The raw ReadScreen/frame-stream video source that once lived here was
deleted on 2026-09-16 with the CPU capture path; the only camera besides
the GPU route is the desktop grab below, which hands over the moment the
GPU backend is discoverable.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from sm64_events.core.profiling import measured
from sm64_events.inputs.frame import (MARIO_BLOCK_OFF, MARIO_BLOCK_SIZE, InputFrame, decode,
                                      valid_raw_stick)
from sm64_events.memory import addresses as A
from sm64_events.memory.addresses import KSEG0_BASE
from sm64_events.memory.base import RdramReader

log = logging.getLogger("sm64.replay")

#: the table's entry order; the frame counter is entry 0 by contract
TABLE_ORDER = ("global_timer", "player1_controller", "mario", "usamune_overall")
#: how often the desktop camera asks whether the GPU backend is discoverable
LAYER_WATCH_S = 1.0


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
    # A controller block the table carried but which is not a controller
    # state (reset/loading memory): the row says so instead of a pad.
    pad_invalid: bool = False
    # Only a layout with a verified Mario address captured yaw/action/
    # speed. Without it the row carries no `mario` at all: 0 degrees is a
    # real bearing and a frame nobody recorded must not claim it.
    mario_captured: bool = False

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
            if self.mario_captured:
                out["mario"] = [self.pad.action, self.pad.yaw, round(self.pad.speed, 3)]
        elif self.pad_invalid:
            out["pad_invalid"] = True
        return out


@measured("capture.decode_stamp")
def decode_stamp(slot, table: list, layout) -> FrameStamp | None:
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
    pad_invalid = False
    mario_captured = "mario" in by_name
    if "player1_controller" in by_name:
        block = memory.read_block(layout.player1_controller, A.CONTROLLER_SIZE)
        mario = (memory.read_block(layout.mario_struct + MARIO_BLOCK_OFF, MARIO_BLOCK_SIZE)
                 if mario_captured else None)
        pad = decode(block, mario)
        # The same refusal the live sampler applies (sampler.py): sign-
        # extended s8 axes and only real button bits. Anything else is
        # readable memory that is not a controller state, never a pad.
        if (not valid_raw_stick(pad.stick_x, pad.stick_y)
                or pad.buttons & ~A.BUTTON_VALID_MASK
                or pad.pressed & ~pad.buttons):
            pad, pad_invalid = None, True
    return FrameStamp(frame=frame, igt_overall=igt, pad=pad, vi_origin=slot.vi_origin,
                      list_qpc=slot.list_qpc, present_qpc=slot.present_qpc,
                      lists_since=slot.lists_since, pad_invalid=pad_invalid,
                      mario_captured=mario_captured and pad is not None)



class DesktopUntilLayerPresents:
    """The camera the recorder gets while the capture layer is not (yet)
    delivering: the desktop grab, with a watch on the GPU backend.

    The moment `backend_ready()` says the wrapper's control page is
    discoverable, this source ENDS ITSELF the way a lost window does --
    `on_stopped` -- so the recorder's attach loop runs its factory again and
    gets the GPU source. Whichever order he opened the game and the trainer
    in, and whenever the ROM loads (his rule, 2026-09-05: "we need to be
    order agnostic"). His first restart attached to Project64's window two
    seconds before the ROM ran; the camera chosen then was never revisited,
    and the layer sat presenting to nobody.

    `note` explains on `frame_source_note` why the desktop is recording (a
    layer that is installed but refuses, or none at all)."""

    frame_source = "desktop"

    def __init__(self, desktop, note: str | None = None, *, backend_ready=None):
        self._desktop = desktop
        self._backend_ready = backend_ready
        self.frame_source_note = note
        self.upgraded = False
        self._on_stopped = None
        self._stop = threading.Event()
        self._thread = None

    def set_idle_check(self, fn) -> None:
        if hasattr(self._desktop, "set_idle_check"):
            self._desktop.set_idle_check(fn)

    def status(self) -> dict | None:
        return self._desktop.status() if hasattr(self._desktop, "status") else None

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

    def stop(self) -> None:
        self.request_stop()
        thread = self._thread
        if (thread is not None and thread is not threading.current_thread()
                and thread.ident is not None):
            thread.join(timeout=2.0)
        self._thread = None
        self._desktop.stop()

    def _watch(self) -> None:
        while not self._stop.wait(LAYER_WATCH_S):
            try:
                ready = self._backend_ready is not None and self._backend_ready()
            except (OSError, RuntimeError, ValueError):
                log.debug("GPU backend status unreadable in the layer watch", exc_info=True)
                continue
            if ready:
                if not self._stop.is_set():
                    self._handover()
                return

    def _handover(self) -> None:
        self.upgraded = True
        log.info("capture backend ready; handing the recorder over to it")
        if self._on_stopped is not None:
            self._on_stopped()
