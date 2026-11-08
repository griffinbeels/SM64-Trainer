"""The Ghost Lab's save-state strip: what Usamune's nine slots hold in the running game, read only (lab only).

    GET /api/scene-lab/slots                  -> {game, reason, loaded, stamp, slots: [nine of {slot, state, level,
                                                  area, place, version, translated, picture, frame, mine}]}
    GET /api/scene-lab/slots/picture/{name}   -> the JPEG the page sent with a Lab write of that slot

Reads only: `SceneSlots` reads through `ReadOnly`, which refuses every write, over the reader's own read-only
process handle (memory/pj64.py: only the moment write opens a handle with write rights, for that one write). The
page polls about every two seconds while the Ghost Lab shows and draws again only when `stamp` changes.

What each answer stands on (Griffin's rule: show only what the data supports):
- "state": the occupied word (usamune.is_occupied); "unknown" for every slot while no Usamune game can be read,
  with "reason" one sentence.
- "level", "area", "place": the slot's own trailer (usamune.slot_facts), in the words the moment refusals use
  (eventlabel.place_name). A slot whose trailer is no place in the game is "saved" with all three null.
- "version", the Usamune build that made the state: the Lab's own write (its answer's build, while the slot still
  holds what that write left); else a Slot Library state with exactly these bytes (KeptState.build); else the
  running game's build, because a console reset zeroes every slot (design.md), so occupied bytes in this game came
  from its own save, from this Lab (marked) or from the refit (which follows the mark). The one writer this cannot
  see is the slot library's command-line install (tools/scene_lab/slot_library.py): a state it translated from the
  other build would read as this game's build.
- "translated", "picture", "frame", "mine": the Lab's own write only. A mark is bound to a hash of the bytes the
  write left (read back after it); the refit's move of those bytes carries it (`observe_refit`), and anything else
  that changes the slot (a save in the game) drops it. "frame" is the package frame the state was built from.
- "loaded", the slot Usamune loaded last: decided at the journal's state_loaded event, never from Usamune's own
  chosen-slot setting (a 2026-11-05 read of it came back 0, so where it lives is not confirmed). Usamune's load
  refuses a slot unless its gMarioObject, level and area equal the running game's, and the event carries Mario's
  action from the first poll after the load (it read the slot's own action live on 2026-11-05, design.md). So the
  candidates are the occupied slots with the game's three trailer fields and the event's action; one is the
  answer; several are told apart by Mario's place only when exactly one saved place is within LOADED_NEAR units of
  where Mario is when the check runs (a few frames after the load); else null. Proved with fakes only: it needs
  his live check (load a slot, see it marked).

Pictures: the page may send a small JPEG of the frame on screen with a write (`picture`). It is untrusted: at most
PICTURE_MAX_BYTES decoded, a JPEG's own start and end markers, else ignored and the write goes on. It is kept under
the lab root's slot-pictures folder by the sha256 of its bytes (never a name from the page), the newest
KEEP_PICTURES of them. The marks and the last load live in the lab root's slot-marks.json, so a server restart
keeps them while the game's slots still hold the same bytes.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from sm64_events.memory.addresses import MARIO_ACTION_OFF, MARIO_POS_OFF
from sm64_events.slotlibrary.store import SlotLibrary, state_id, write_atomic
from sm64_events.slotlibrary.usamune import (
    SLOT_BYTES,
    SLOT_COUNT,
    Refusal,
    check_cartridge,
    is_occupied,
    live_facts,
    slot_address,
    slot_facts,
)
from sm64_events.tracking.eventlabel import place_name

log = logging.getLogger("sm64.scene_lab")

PICTURE_MAX_BYTES = 64 << 10           # a 160 x 120 JPEG is about 5 KB; anything near this is not one
PICTURE_MAX_TEXT = 200_000             # base64 text past this is not decoded at all
KEEP_PICTURES = 64
LOADED_NEAR = 200.0                    # world units: a few frames of Mario's fastest running
MARKS_FILE, PICTURES_FOLDER, MARKS_VERSION = "slot-marks.json", "slot-pictures", 1
PICTURE_NAME = re.compile(r"[0-9a-f]{64}")
_JPEG_START, _JPEG_END = b"\xff\xd8\xff", b"\xff\xd9"
NO_GAME = "Project64 is not running a game."


def slots_sentence(text: str) -> str:
    """Why the slots cannot be read, for a person at the page (scene_moments.game_sentence without "press again")."""
    if "no Project64" in text or "no cartridge" in text:
        return NO_GAME
    if "is not Usamune" in text:
        return "The running game is not Usamune."
    if "has not checked" in text:
        return "This Trainer cannot read this Usamune's save states yet."
    if "8 MB" in text:
        return "The game runs without the 8 MB of RAM that Usamune's save states need."
    return "The game's save states cannot be read right now."


class ReadOnly:
    """A GameMemory that can only read: the strip never writes, whatever it is handed."""

    def __init__(self, memory):
        self.memory = memory

    def read(self, address: int, size: int) -> bytes:
        return self.memory.read(address, size)

    def write(self, address: int, data: bytes) -> None:
        raise PermissionError("the save-state strip only reads the game")

    def cartridge_header(self) -> bytes | None:
        return self.memory.cartridge_header()


@dataclass(frozen=True)
class Mark:
    """What the Lab knows about bytes it wrote into a slot: bound to `digest`, the sha256 of the slot's bytes."""
    digest: str
    version: str | None
    translated: bool
    picture: str | None
    frame: int | None


def picture_bytes(encoded) -> bytes | None:
    """The page's JPEG, or None for anything else: too long, not base64, too big decoded, or not a JPEG."""
    if not isinstance(encoded, str) or not encoded or len(encoded) > PICTURE_MAX_TEXT:
        return None
    if encoded.startswith("data:"):
        head, _, encoded = encoded.partition(",")
        if head != "data:image/jpeg;base64":
            return None
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(data) > PICTURE_MAX_BYTES or not data.startswith(_JPEG_START) or not data.rstrip(b"\0").endswith(_JPEG_END):
        return None
    return data


class SlotPictures:
    """The pictures the page sent with Lab writes, by the sha256 of their bytes, the newest KEEP_PICTURES kept."""

    def __init__(self, folder):
        self._folder = folder                  # a Path, or a callable giving one (the lab root is read late)
        self._lock = threading.Lock()

    @property
    def folder(self) -> Path:
        return Path(self._folder() if callable(self._folder) else self._folder)

    def keep(self, data: bytes) -> str:
        name = hashlib.sha256(data).hexdigest()
        with self._lock:
            folder = self.folder
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{name}.jpg"
            if not path.exists():
                write_atomic(path, data)
            # Newest by its stamp: two pictures kept within one tick of the file system's clock still get an order.
            self._last_ns = max(time.time_ns(), getattr(self, "_last_ns", 0) + 1000)
            os.utime(path, ns=(self._last_ns, self._last_ns))
            kept = sorted(folder.glob("*.jpg"), key=lambda one: one.stat().st_mtime_ns, reverse=True)
            for old in kept[KEEP_PICTURES:]:
                old.unlink(missing_ok=True)
        return name

    def path(self, name: str) -> Path | None:
        if not isinstance(name, str) or not PICTURE_NAME.fullmatch(name):
            return None
        path = self.folder / f"{name}.jpg"
        return path if path.is_file() else None


def _empty_slot(slot: int, state: str) -> dict:
    return {"slot": slot, "state": state, "level": None, "area": None, "place": None, "version": None,
            "translated": False, "picture": None, "frame": None, "mine": False}


def _action_and_place(data: bytes) -> tuple[int, tuple[float, float, float]]:
    """Mario's action and place as the slot's body saved them (the body is a copy from gMarioStates)."""
    return (struct.unpack_from(">I", data, MARIO_ACTION_OFF)[0],
            struct.unpack_from(">3f", data, MARIO_POS_OFF))


class SceneSlots:
    """The strip's reads, the Lab's marks on the slots it wrote, and the last load.

    `game()` attaches the running game (a GameMemory; Refusal without one), wrapped in ReadOnly and kept between
    polls; `library` is a SlotLibrary or a callable giving one; `root()` the lab root. `run(work)` runs a load's
    check off the poll loop (a one-thread pool; tests run it at once)."""

    def __init__(self, *, game, library=None, root=None, run=None):
        self.game, self._library, self.root = game, library, root
        self._run = run
        self._pool: ThreadPoolExecutor | None = None
        self._memory: ReadOnly | None = None
        self._read_lock = threading.Lock()        # one reader, one read at a time
        self._lock = threading.Lock()             # the marks and the last load
        self.marks: dict[int, Mark] = {}          # slot index -> what the Lab wrote there
        self.loaded: tuple[int, str] | None = None   # (slot index, the digest it held when it was loaded)
        self._kept_builds: dict[str, str] = {}    # digest -> build, for library states already looked up
        self._read_marks = False
        self.pictures = SlotPictures(lambda: self._root() / PICTURES_FOLDER)

    # ------------------------------------------------------------ where things live

    def _root(self) -> Path:
        if self.root is None:
            raise RuntimeError("the save-state strip has no lab root")
        return Path(self.root() if callable(self.root) else self.root)

    def library(self) -> SlotLibrary | None:
        if callable(self._library) and not isinstance(self._library, SlotLibrary):
            self._library = self._library()
        return self._library

    def _load_marks(self) -> None:
        """slot-marks.json, once: what a previous server knew about the slots its Lab wrote."""
        if self._read_marks or self.root is None:
            return
        self._read_marks = True
        try:
            saved = json.loads((self._root() / MARKS_FILE).read_text(encoding="utf-8"))
            if saved.get("version") != MARKS_VERSION:
                return
            for key, value in (saved.get("marks") or {}).items():
                index = int(key) - 1
                if 0 <= index < SLOT_COUNT and PICTURE_NAME.fullmatch(str(value.get("digest"))):
                    picture = value.get("picture")
                    self.marks[index] = Mark(value["digest"], value.get("version") if value.get("version") in ("us", "jp")
                                             else None, bool(value.get("translated")),
                                             picture if isinstance(picture, str) and PICTURE_NAME.fullmatch(picture) else None,
                                             value.get("frame") if type(value.get("frame")) is int else None)
            loaded = saved.get("loaded")
            if isinstance(loaded, dict) and type(loaded.get("slot")) is int and 1 <= loaded["slot"] <= SLOT_COUNT \
                    and PICTURE_NAME.fullmatch(str(loaded.get("digest"))):
                self.loaded = (loaded["slot"] - 1, loaded["digest"])
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            log.warning("The save-state strip's marks could not be read; it starts without them.", exc_info=True)

    def _save_marks(self) -> None:
        if self.root is None:
            return
        record = {"version": MARKS_VERSION,
                  "marks": {str(index + 1): asdict(mark) for index, mark in sorted(self.marks.items())},
                  "loaded": {"slot": self.loaded[0] + 1, "digest": self.loaded[1]} if self.loaded else None}
        try:
            root = self._root()
            root.mkdir(parents=True, exist_ok=True)
            write_atomic(root / MARKS_FILE, json.dumps(record, indent=1).encode("utf-8"))
        except OSError:
            log.warning("The save-state strip's marks could not be saved.", exc_info=True)

    # ------------------------------------------------------------ the game

    def _reader(self) -> ReadOnly:
        if self._memory is None:
            self._memory = ReadOnly(self.game())
        return self._memory

    def _drop_reader(self) -> None:
        memory, self._memory = self._memory, None
        detach = getattr(getattr(getattr(memory, "memory", None), "reader", None), "detach", None)
        if detach is not None:
            try:
                detach()
            except Exception:  # noqa: BLE001 - a reader that is gone is gone
                log.debug("A slot reader did not detach.", exc_info=True)

    def _slots_now(self):
        """(build, [nine slot bytes]) from the running game; Refusal (or an OS error) without one. Holds the
        read lock."""
        memory = self._reader()
        build = check_cartridge(memory)
        return memory, build, [memory.read(slot_address(index), SLOT_BYTES) for index in range(SLOT_COUNT)]

    # ------------------------------------------------------------ the strip's answer

    def read_blocking(self) -> dict:
        """GET /slots. Blocking: a route runs it on a thread."""
        with self._lock:
            self._load_marks()
        with self._read_lock:
            try:
                _memory, build, datas = self._slots_now()
            except Refusal as refused:
                self._drop_reader()
                return self._answer(None, slots_sentence(str(refused)), [_empty_slot(n, "unknown") for n in range(1, 10)])
            except Exception:  # noqa: BLE001 - a game that went away mid-read is a game not running
                log.debug("The save-state strip could not read the game.", exc_info=True)
                self._drop_reader()
                return self._answer(None, NO_GAME, [_empty_slot(n, "unknown") for n in range(1, 10)])
        digests = [state_id(data) if is_occupied(data) else None for data in datas]
        with self._lock:
            marks = dict(self.marks)
            loaded = self.loaded
        slots = [self._slot(index, datas[index], digests[index], marks.get(index), build.version)
                 for index in range(SLOT_COUNT)]
        loaded_slot = loaded[0] + 1 if loaded and digests[loaded[0]] == loaded[1] else None
        return self._answer(build.version, None, slots, loaded_slot)

    def _slot(self, index: int, data: bytes, digest: str | None, mark: Mark | None, running: str) -> dict:
        if digest is None:
            return _empty_slot(index + 1, "empty")
        answer = _empty_slot(index + 1, "saved")
        try:
            facts = slot_facts(data)
        except Refusal:
            return answer                                  # occupied, but no place in the game: nothing to name
        answer.update(level=facts.level, area=facts.area, place=place_name(facts.level, facts.area))
        if mark is not None and mark.digest == digest:
            picture = mark.picture if mark.picture and self.pictures.path(mark.picture) else None
            return answer | {"version": mark.version, "translated": mark.translated, "mine": True,
                             "picture": f"/api/scene-lab/slots/picture/{picture}" if picture else None,
                             "frame": mark.frame}
        return answer | {"version": self._kept_build(digest) or running}

    def _kept_build(self, digest: str) -> str | None:
        """The build a Slot Library state with exactly these bytes names; None when the library has none."""
        if digest in self._kept_builds:
            return self._kept_builds[digest]
        library = self.library()
        if library is None:
            return None
        try:
            if not library.has(digest):
                return None
            build = library.get(digest).build
        except Exception:  # noqa: BLE001 - a damaged record says nothing about these bytes
            log.debug("A Slot Library record could not be read for the strip.", exc_info=True)
            return None
        if build is not None:
            self._kept_builds[digest] = build
        return build

    @staticmethod
    def _answer(game, reason, slots, loaded=None) -> dict:
        body = {"game": game, "reason": reason, "loaded": loaded, "slots": slots}
        stamp = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        return {**body, "stamp": stamp}

    # ------------------------------------------------------------ the Lab's own writes

    def note_written(self, memory, slot: int, written: dict, picture: bytes | None) -> None:
        """After a Lab write that succeeded: bind what it wrote (its build, translation, the page's picture and the
        package frame) to the bytes the slot now holds, read back. Never raises: the write is done either way."""
        try:
            data = memory.read(slot_address(slot - 1), SLOT_BYTES)
            name = self.pictures.keep(picture) if picture is not None else None
            mark = Mark(state_id(data), written.get("build") if written.get("build") in ("us", "jp") else None,
                        bool(written.get("translated")), name,
                        written.get("frame") if type(written.get("frame")) is int else None)
            with self._lock:
                self._load_marks()
                self.marks[slot - 1] = mark
                self._save_marks()
        except Exception:  # noqa: BLE001 - the slot is written; the strip's note of it is a nicety
            log.warning("The save-state strip did not note slot %s.", slot, exc_info=True)

    def observe_refit(self, report) -> None:
        """The slot refit moved Mario inside a slot's bytes (slotlibrary/refit.py): a mark or the last load bound to
        the bytes before the move follows them."""
        written = dict(getattr(report, "written", ()) or ())
        moved = {index: (state_id(before), state_id(written[index]))
                 for index, before in (getattr(report, "originals", ()) or ()) if index in written}
        if not moved:
            return
        with self._lock:
            self._load_marks()
            changed = False
            for index, (before, after) in moved.items():
                mark = self.marks.get(index)
                if mark is not None and mark.digest == before:
                    self.marks[index] = replace(mark, digest=after)
                    changed = True
                if self.loaded == (index, before):
                    self.loaded = (index, after)
                    changed = True
            if changed:
                self._save_marks()

    # ------------------------------------------------------------ the last load

    def observe_event(self, event) -> None:
        """The broadcaster's observer, on the poll loop: a state_loaded event queues the check and returns."""
        if getattr(event, "type", None) != "state_loaded":
            return
        action = (getattr(event, "payload", None) or {}).get("action")
        if type(action) is not int:
            return
        if self._run is not None:
            self._run(lambda: self.identify_load(action))
            return
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="slot-strip")
        self._pool.submit(self.identify_load, action)

    def identify_load(self, action: int) -> int | None:
        """Which slot the load that left Mario in `action` came from (see the module's docstring), remembered as the
        last load; None, and the last load forgotten, when the evidence does not name exactly one."""
        try:
            with self._read_lock:
                memory, build, datas = self._slots_now()
                live = live_facts(memory, build)
                here = struct.unpack(">3f", memory.read(build.body_start + MARIO_POS_OFF, 12))
        except Exception:  # noqa: BLE001 - no game, no answer
            log.debug("The last load could not be checked.", exc_info=True)
            with self._read_lock:
                self._drop_reader()
            return None
        candidates = []
        for index, data in enumerate(datas):
            if not is_occupied(data):
                continue
            try:
                facts = slot_facts(data)
            except Refusal:
                continue
            saved_action, saved_place = _action_and_place(data)
            if facts == live and saved_action == action:
                candidates.append((index, saved_place))
        if len(candidates) > 1:
            near = [(index, place) for index, place in candidates
                    if sum((a - b) ** 2 for a, b in zip(place, here, strict=True)) ** 0.5 <= LOADED_NEAR]
            candidates = near
        found = candidates[0][0] if len(candidates) == 1 else None
        with self._lock:
            self._load_marks()
            self.loaded = (found, state_id(datas[found])) if found is not None else None
            self._save_marks()
        return None if found is None else found + 1

    def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
        with self._read_lock:
            self._drop_reader()
