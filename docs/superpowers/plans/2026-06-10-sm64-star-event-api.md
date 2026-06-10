# SM64 Star Event API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python server that attaches to Project64 1.6, detects every star grab (including re-collections) in SM64 Usamune v1.93u with frame-accurate timing, and broadcasts `star_collected` events to all WebSocket listeners.

**Architecture:** External process-memory polling (~60 Hz) → coherent `GameSnapshot` per tick → pure `(prev, curr) → events` detectors → FastAPI WebSocket broadcast. Star grabs are detected as an *edge* into Mario's star-dance actions; star identity comes from the game's `gLastCompletedCourseNum`/`gLastCompletedStarNum` globals (updated on every collection, including repeats); the exact grab frame is back-computed as `gGlobalTimer − actionTimer`.

**Tech Stack:** Python 3.12 (uv-managed), pymem (ReadProcessMemory), FastAPI + uvicorn (WebSocket + HTTP), pytest.

**Spec:** `docs/superpowers/specs/2026-06-10-sm64-star-event-api-design.md`

---

## Domain background (read this first)

Things a skilled engineer won't know about this domain:

1. **PJ64 1.6 has no API.** We attach to `Project64.exe` with `ReadProcessMemory` and read the emulated N64 RAM (RDRAM) directly. This is how STROOP and AutoSplit64 work. Read-only; never write.
2. **Endianness quirk.** The N64 is big-endian; PJ64 stores RDRAM as **little-endian 32-bit words**. To read N64 bytes from the host buffer: byte at N64 offset `o` lives at host offset `o ^ 3`; an aligned halfword at `o ^ 2`; an aligned word reads directly as a little-endian u32. This logic lives in exactly one place (`memory/base.py`).
3. **Finding RDRAM.** PJ64 allocates RDRAM with VirtualAlloc somewhere in its address space. We enumerate committed memory regions and test each ≥ 4 MB region against the libultra `osBootConfig` signature, which every N64 game writes at fixed low RDRAM addresses: u32 at N64 `0x80000308` == `0xB0000000` (cart ROM base), u32 at `0x80000318` ∈ {`0x400000`, `0x800000`} (RDRAM size), u32 at `0x80000300` ≤ 2 (TV type).
4. **N64 addresses.** Game symbols are quoted as KSEG0 virtual addresses (`0x80xxxxxx`). RDRAM offset = address − `0x80000000`.
5. **Game logic runs at 30 fps.** `gGlobalTimer` increments once per game frame. All event timing is in game frames; wall clock is metadata.
6. **Why the detection works for re-collections:** in the game's interaction handler (`interact_star_or_key` in the decomp), `save_file_collect_star_or_key()` runs *before* Mario's action is set to a star-dance action — on **every** grab, already-owned or not. So at the moment we observe the action edge, `gLastCompletedCourseNum`/`gLastCompletedStarNum` (both **1-based**) already identify the star, and Mario's `numStars` has already incremented (or not, if it was a repeat — which is exactly our `already_collected` signal).
7. **VERIFY addresses.** Addresses marked `VERIFY` in the registry are well-known community values for the US ROM (Usamune is US-based) but must pass the live harness (Task 12) before the server is trusted. Cross-check sources on mismatch: ukikipedia.net/wiki/RAM, the SM64 decomp US symbol map, STROOP's mapping tables.
8. **Known limitation (accepted for goal one):** Bowser-stage key grabs also use star-dance actions, so they may emit a `star_collected` with `course_id` 16/17. Documented; a dedicated key event type comes later. Grand stars use a different cutscene action and are correctly excluded.

## File structure

```
sm64_tracker/
├── pyproject.toml
├── README.md
├── .gitignore
├── src/sm64_events/
│   ├── __init__.py
│   ├── main.py                  # wiring + uvicorn entry
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── addresses.py         # THE registry: addresses, action IDs, name tables
│   │   ├── base.py              # N64Memory Protocol + RdramReader (endian logic)
│   │   ├── buffer.py            # BufferMemory test double (PJ64-style buffer)
│   │   └── pj64.py              # process attach, RDRAM scan, live reads
│   ├── core/
│   │   ├── __init__.py
│   │   ├── snapshot.py          # GameSnapshot + SnapshotReader
│   │   ├── events.py            # Event + wire format
│   │   └── logging_setup.py     # persistent file logging, UTC
│   ├── detectors/
│   │   ├── __init__.py
│   │   ├── base.py              # Detector Protocol
│   │   ├── star_grab.py         # goal-one detector
│   │   └── lifecycle.py         # game_reset (timer backward jump)
│   └── server/
│       ├── __init__.py
│       ├── broadcaster.py       # WS fan-out, seq numbers, drop dead clients
│       ├── poller.py            # 60 Hz loop, attach/retry, tick()
│       └── app.py               # FastAPI: /ws/events, /health, /state
├── tools/
│   └── verify_addresses.py      # live address verification harness
└── tests/
    ├── test_addresses.py
    ├── test_memory.py
    ├── test_snapshot.py
    ├── test_events.py
    ├── test_star_grab.py
    ├── test_lifecycle.py
    ├── test_pj64_signature.py
    ├── test_broadcaster.py
    ├── test_poller.py
    └── test_app.py
```

---

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`, `README.md`, `.gitignore`, `src/sm64_events/__init__.py`, plus empty `__init__.py` in `memory/`, `core/`, `detectors/`, `server/`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "sm64-events"
version = "0.1.0"
description = "Event API for SM64 (Usamune v1.93u) running in Project64 1.6"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "pymem>=1.13",
]

[dependency-groups]
dev = ["pytest>=8.0", "httpx>=0.27"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/sm64_events"]
```

- [ ] **Step 2: Write `.gitignore`**

```gitignore
.venv/
__pycache__/
*.pyc
.pytest_cache/
logs/
```

- [ ] **Step 3: Write `README.md`**

```markdown
# sm64_tracker — SM64 Event API

Detects star grabs in SM64 Usamune v1.93u running in Project64 1.6 and
broadcasts them as WebSocket events. See `docs/superpowers/specs/` for design.

## Run

    uv sync
    uv run uvicorn sm64_events.main:app --host 127.0.0.1 --port 8064

Requires Project64 1.6 running Usamune v1.93u on the same machine.

- Events: `ws://127.0.0.1:8064/ws/events`
- Health: `http://127.0.0.1:8064/health`
- Latest snapshot: `http://127.0.0.1:8064/state`
```

- [ ] **Step 4: Create the package skeleton and sync**

Create `src/sm64_events/__init__.py` (empty), and empty `__init__.py` files in `src/sm64_events/memory/`, `src/sm64_events/core/`, `src/sm64_events/detectors/`, `src/sm64_events/server/`. Create empty `tests/` and `tools/` directories.

Run: `uv sync`
Expected: venv created, dependencies installed.

Run: `uv run python -c "import sm64_events; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock README.md .gitignore src
git commit -m "chore: scaffold sm64-events package with uv"
```

---

### Task 2: Address registry and name tables

**Files:**
- Create: `src/sm64_events/memory/addresses.py`
- Test: `tests/test_addresses.py`

This is the single authoritative registry (schema-driven design): every address, struct offset, action constant, and ID→name mapping lives here and nowhere else.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_addresses.py
from sm64_events.memory import addresses as A


def test_star_grab_action_set_contains_all_dance_variants():
    assert A.ACT_STAR_DANCE_EXIT in A.STAR_GRAB_ACTIONS
    assert A.ACT_STAR_DANCE_WATER in A.STAR_GRAB_ACTIONS
    assert A.ACT_STAR_DANCE_NO_EXIT in A.STAR_GRAB_ACTIONS
    assert A.ACT_FALL_AFTER_STAR_GRAB in A.STAR_GRAB_ACTIONS


def test_course_names():
    assert A.course_name(1) == "Bob-omb Battlefield"
    assert A.course_name(15) == "Rainbow Ride"
    assert A.course_name(99) == "Course 99"  # graceful fallback


def test_star_names_main_course():
    assert A.star_name(1, 2) == "Shoot to the Island in the Sky"
    assert A.star_name(1, 6) == "100 Coins"
    assert A.star_name(14, 0) == "Roll into the Cage"


def test_star_names_fallback():
    assert A.star_name(99, 0) == "Star 1"
    assert A.star_name(1, 9) == "Star 10"


def test_mario_offsets_derive_from_struct_base():
    assert A.MARIO_ACTION == A.MARIO_STRUCT + 0x0C
    assert A.MARIO_ACTION_TIMER == A.MARIO_STRUCT + 0x1A
    assert A.MARIO_NUM_STARS == A.MARIO_STRUCT + 0xAA
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_addresses.py -q`
Expected: FAIL — `ImportError`/`AttributeError` (module has no contents yet).

- [ ] **Step 3: Write `addresses.py`**

```python
# src/sm64_events/memory/addresses.py
"""Single authoritative registry of SM64 memory locations and ID->name tables.

ROM: SM64 US / Usamune v1.93u (Usamune is built on the US ROM).
All addresses are N64 KSEG0 virtual addresses (0x80000000-based).

Entries marked VERIFY must pass tools/verify_addresses.py before the server
is trusted with real sessions. Cross-check sources on mismatch:
  - https://ukikipedia.net/wiki/RAM (US column)
  - SM64 decomp US symbol map (sm64.us.map build artifact)
  - STROOP mapping tables (github.com/SM64-TAS-ABC/STROOP)
"""

KSEG0_BASE = 0x80000000
RDRAM_MIN_SIZE = 0x400000  # 4 MB; SM64 runs without the expansion pak

# libultra osBootConfig — identical for every N64 game; used to find RDRAM.
OS_TV_TYPE = 0x80000300   # u32: 0 PAL, 1 NTSC, 2 MPAL
OS_ROM_BASE = 0x80000308  # u32: 0xB0000000 for cartridge boot
OS_MEM_SIZE = 0x80000318  # u32: 0x400000 or 0x800000

# Mario state (gMarioStates[0]) — source: decomp struct MarioState + STROOP US.
MARIO_STRUCT = 0x8033B170
MARIO_ACTION = MARIO_STRUCT + 0x0C        # u32
MARIO_ACTION_TIMER = MARIO_STRUCT + 0x1A  # u16, resets to 0 on action change
MARIO_NUM_STARS = MARIO_STRUCT + 0xAA     # s16, total star count  VERIFY

GLOBAL_TIMER = 0x8032D5D4            # u32, +1 per game frame (30 Hz)  VERIFY
LAST_COMPLETED_COURSE = 0x8032DDF8   # s8, 1-based, 0 = none yet  VERIFY
LAST_COMPLETED_STAR = 0x8032DDF9     # s8, 1-based  VERIFY

# Mario actions entered the moment a star (or key) is grabbed — decomp sm64.h.
ACT_STAR_DANCE_EXIT = 0x00001302
ACT_STAR_DANCE_WATER = 0x00001303
ACT_STAR_DANCE_NO_EXIT = 0x00001307
ACT_FALL_AFTER_STAR_GRAB = 0x00001904  # midair grabs  VERIFY

STAR_GRAB_ACTIONS = frozenset({
    ACT_STAR_DANCE_EXIT,
    ACT_STAR_DANCE_WATER,
    ACT_STAR_DANCE_NO_EXIT,
    ACT_FALL_AFTER_STAR_GRAB,
})

# ---------------------------------------------------------------------------
# Name tables (display-only; IDs are the authoritative identity).
# ---------------------------------------------------------------------------

COURSE_NAMES = {
    0: "Castle Secret",
    1: "Bob-omb Battlefield",
    2: "Whomp's Fortress",
    3: "Jolly Roger Bay",
    4: "Cool, Cool Mountain",
    5: "Big Boo's Haunt",
    6: "Hazy Maze Cave",
    7: "Lethal Lava Land",
    8: "Shifting Sand Land",
    9: "Dire, Dire Docks",
    10: "Snowman's Land",
    11: "Wet-Dry World",
    12: "Tall, Tall Mountain",
    13: "Tiny-Huge Island",
    14: "Tick Tock Clock",
    15: "Rainbow Ride",
    16: "Bowser in the Dark World",
    17: "Bowser in the Fire Sea",
    18: "Bowser in the Sky",
    19: "The Princess's Secret Slide",
    20: "Cavern of the Metal Cap",
    21: "Tower of the Wing Cap",
    22: "Vanish Cap Under the Moat",
    23: "Wing Mario Over the Rainbow",
    24: "The Secret Aquarium",
}

STAR_NAMES = {
    1: ("Big Bob-omb on the Summit", "Footrace with Koopa the Quick",
        "Shoot to the Island in the Sky", "Find the 8 Red Coins",
        "Mario Wings to the Sky", "Behind Chain Chomp's Gate"),
    2: ("Chip off Whomp's Block", "To the Top of the Fortress",
        "Shoot into the Wild Blue", "Red Coins on the Floating Isle",
        "Fall onto the Caged Island", "Blast Away the Wall"),
    3: ("Plunder in the Sunken Ship", "Can the Eel Come Out to Play?",
        "Treasure of the Ocean Cave", "Red Coins on the Ship Afloat",
        "Blast to the Stone Pillar", "Through the Jet Stream"),
    4: ("Slip Slidin' Away", "Li'l Penguin Lost", "Big Penguin Race",
        "Frosty Slide for 8 Red Coins", "Snowman's Lost His Head",
        "Wall Kicks Will Work"),
    5: ("Go on a Ghost Hunt", "Ride Big Boo's Merry-Go-Round",
        "Secret of the Haunted Books", "Seek the 8 Red Coins",
        "Big Boo's Balcony", "Eye to Eye in the Secret Room"),
    6: ("Swimming Beast in the Cavern", "Elevate for 8 Red Coins",
        "Metal-Head Mario Can Move!", "Navigating the Toxic Maze",
        "A-Maze-Ing Emergency Exit", "Watch for Rolling Rocks"),
    7: ("Boil the Big Bully", "Bully the Bullies",
        "8-Coin Puzzle with 15 Pieces", "Red-Hot Log Rolling",
        "Hot-Foot-It into the Volcano", "Elevator Tour in the Volcano"),
    8: ("In the Talons of the Big Bird", "Shining Atop the Pyramid",
        "Inside the Ancient Pyramid", "Stand Tall on the Four Pillars",
        "Free Flying for 8 Red Coins", "Pyramid Puzzle"),
    9: ("Board Bowser's Sub", "Chests in the Current",
        "Pole-Jumping for Red Coins", "Through the Jet Stream",
        "The Manta Ray's Reward", "Collect the Caps..."),
    10: ("Snowman's Big Head", "Chill with the Bully", "In the Deep Freeze",
         "Whirl from the Freezing Pond", "Shell Shreddin' for Red Coins",
         "Into the Igloo"),
    11: ("Shocking Arrow Lifts!", "Top o' the Town",
         "Secrets in the Shallows & Sky", "Express Elevator--Hurry Up!",
         "Go to Town for Red Coins", "Quick Race Through Downtown!"),
    12: ("Scale the Mountain", "Mystery of the Monkey Cage",
         "Scary 'Shrooms, Red Coins", "Mysterious Mountainside",
         "Breathtaking View from Bridge", "Blast to the Lonely Mushroom"),
    13: ("Pluck the Piranha Flower", "The Tip Top of the Huge Island",
         "Rematch with Koopa the Quick", "Five Itty Bitty Secrets",
         "Wiggler's Red Coins", "Make Wiggler Squirm"),
    14: ("Roll into the Cage", "The Pit and the Pendulums", "Get a Hand",
         "Stomp on the Thwomp", "Timed Jumps on Moving Bars",
         "Stop Time for Red Coins"),
    15: ("Cruiser Crossing the Rainbow", "The Big House in the Sky",
         "Coins Amassed in a Maze", "Swingin' in the Breeze",
         "Tricky Triangles!", "Somewhere Over the Rainbow"),
    16: ("8 Red Coins",),
    17: ("8 Red Coins",),
    18: ("8 Red Coins",),
    19: ("Slide Star", "Slide Star (Under 21 Seconds)"),
    20: ("8 Red Coins",),
    21: ("8 Red Coins",),
    22: ("8 Red Coins",),
    23: ("8 Red Coins",),
    24: ("8 Red Coins",),
}


def course_name(course_id: int) -> str:
    return COURSE_NAMES.get(course_id, f"Course {course_id}")


def star_name(course_id: int, star_id: int) -> str:
    if 1 <= course_id <= 15 and star_id == 6:
        return "100 Coins"
    names = STAR_NAMES.get(course_id, ())
    if 0 <= star_id < len(names):
        return names[star_id]
    return f"Star {star_id + 1}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_addresses.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/memory/addresses.py tests/test_addresses.py
git commit -m "feat: address registry with star/course name tables"
```

---

### Task 3: Memory protocol, endian decoding, and buffer test double

**Files:**
- Create: `src/sm64_events/memory/base.py`, `src/sm64_events/memory/buffer.py`
- Test: `tests/test_memory.py`

`RdramReader` owns the PJ64 byte-order quirk in one place. `BufferMemory` is a test double that stores bytes exactly the way PJ64 does, so tests exercise the *real* decode path.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_memory.py
import pytest

from sm64_events.memory.buffer import BufferMemory


@pytest.fixture
def mem():
    return BufferMemory()


def test_u32_roundtrip(mem):
    mem.write_u32(0x80001000, 0x11223344)
    assert mem.read_u32(0x80001000) == 0x11223344


def test_bytes_within_word_are_big_endian_as_n64_sees_them(mem):
    mem.write_u32(0x80001000, 0x11223344)
    assert mem.read_u8(0x80001000) == 0x11
    assert mem.read_u8(0x80001001) == 0x22
    assert mem.read_u8(0x80001002) == 0x33
    assert mem.read_u8(0x80001003) == 0x44


def test_u16_halves_of_word(mem):
    mem.write_u32(0x80001000, 0x11223344)
    assert mem.read_u16(0x80001000) == 0x1122
    assert mem.read_u16(0x80001002) == 0x3344


def test_u8_roundtrip_at_odd_address(mem):
    mem.write_u8(0x80002001, 0xAB)
    assert mem.read_u8(0x80002001) == 0xAB


def test_signed_reads(mem):
    mem.write_u8(0x80003000, 0xFF)
    assert mem.read_s8(0x80003000) == -1
    mem.write_u16(0x80003002, 0x8000)
    assert mem.read_s16(0x80003002) == -32768
    mem.write_u16(0x80003004, 0x0042)
    assert mem.read_s16(0x80003004) == 0x42
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_memory.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sm64_events.memory.buffer'`.

- [ ] **Step 3: Write `base.py`**

```python
# src/sm64_events/memory/base.py
"""Typed reads over PJ64's RDRAM image.

PJ64 stores the (big-endian) N64 RDRAM as little-endian 32-bit words.
N64 byte at offset o  -> host offset o ^ 3
aligned halfword at o -> host offset o ^ 2, little-endian
aligned word at o     -> host offset o, little-endian
This module is the ONLY place that knows this.
"""
from typing import Protocol

from sm64_events.memory.addresses import KSEG0_BASE


class MemoryReadError(RuntimeError):
    """Raised when the emulator's memory cannot be read (e.g. it closed)."""


class N64Memory(Protocol):
    def read_u8(self, addr: int) -> int: ...
    def read_u16(self, addr: int) -> int: ...
    def read_u32(self, addr: int) -> int: ...
    def read_s8(self, addr: int) -> int: ...
    def read_s16(self, addr: int) -> int: ...


class RdramReader:
    """Mixin implementing N64Memory over _read_raw(host_offset, size)."""

    def _read_raw(self, offset: int, size: int) -> bytes:
        raise NotImplementedError

    def read_u32(self, addr: int) -> int:
        return int.from_bytes(self._read_raw(addr - KSEG0_BASE, 4), "little")

    def read_u16(self, addr: int) -> int:
        return int.from_bytes(self._read_raw((addr - KSEG0_BASE) ^ 2, 2), "little")

    def read_u8(self, addr: int) -> int:
        return self._read_raw((addr - KSEG0_BASE) ^ 3, 1)[0]

    def read_s8(self, addr: int) -> int:
        v = self.read_u8(addr)
        return v - 0x100 if v >= 0x80 else v

    def read_s16(self, addr: int) -> int:
        v = self.read_u16(addr)
        return v - 0x10000 if v >= 0x8000 else v
```

- [ ] **Step 4: Write `buffer.py`**

```python
# src/sm64_events/memory/buffer.py
"""In-memory RDRAM image laid out exactly like PJ64's (LE 32-bit words).

Test double for N64Memory; also used by snapshot/detector tests so the real
endian decode path is always exercised.
"""
from sm64_events.memory.addresses import KSEG0_BASE, RDRAM_MIN_SIZE
from sm64_events.memory.base import RdramReader


class BufferMemory(RdramReader):
    def __init__(self, size: int = RDRAM_MIN_SIZE):
        self._buf = bytearray(size)

    def _read_raw(self, offset: int, size: int) -> bytes:
        return bytes(self._buf[offset:offset + size])

    def write_u32(self, addr: int, value: int) -> None:
        off = addr - KSEG0_BASE
        self._buf[off:off + 4] = value.to_bytes(4, "little")

    def write_u16(self, addr: int, value: int) -> None:
        off = (addr - KSEG0_BASE) ^ 2
        self._buf[off:off + 2] = value.to_bytes(2, "little")

    def write_u8(self, addr: int, value: int) -> None:
        self._buf[(addr - KSEG0_BASE) ^ 3] = value
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_memory.py -q`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/memory/base.py src/sm64_events/memory/buffer.py tests/test_memory.py
git commit -m "feat: N64Memory protocol with PJ64 endian decoding + buffer test double"
```

---

### Task 4: GameSnapshot and SnapshotReader

**Files:**
- Create: `src/sm64_events/core/snapshot.py`
- Test: `tests/test_snapshot.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_snapshot.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import SnapshotReader
from sm64_events.memory import addresses as A
from sm64_events.memory.buffer import BufferMemory


def test_reader_populates_all_fields():
    mem = BufferMemory()
    mem.write_u32(A.GLOBAL_TIMER, 81234)
    mem.write_u32(A.MARIO_ACTION, A.ACT_STAR_DANCE_EXIT)
    mem.write_u16(A.MARIO_ACTION_TIMER, 2)
    mem.write_u16(A.MARIO_NUM_STARS, 57)
    mem.write_u8(A.LAST_COMPLETED_COURSE, 1)
    mem.write_u8(A.LAST_COMPLETED_STAR, 3)

    snap = SnapshotReader(mem).read()

    assert snap.global_timer == 81234
    assert snap.mario_action == A.ACT_STAR_DANCE_EXIT
    assert snap.mario_action_timer == 2
    assert snap.num_stars == 57
    assert snap.last_completed_course == 1
    assert snap.last_completed_star == 3
    assert snap.wall_time_utc.tzinfo == timezone.utc
    assert (datetime.now(timezone.utc) - snap.wall_time_utc).total_seconds() < 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_snapshot.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sm64_events.core.snapshot'`.

- [ ] **Step 3: Write `snapshot.py`**

```python
# src/sm64_events/core/snapshot.py
"""One coherent read of all game state the detectors need."""
from dataclasses import dataclass
from datetime import datetime, timezone

from sm64_events.memory import addresses as A
from sm64_events.memory.base import N64Memory


@dataclass(frozen=True)
class GameSnapshot:
    wall_time_utc: datetime
    global_timer: int
    mario_action: int
    mario_action_timer: int
    num_stars: int
    last_completed_course: int  # 1-based; 0 = no star collected yet
    last_completed_star: int    # 1-based


class SnapshotReader:
    def __init__(self, mem: N64Memory):
        self._mem = mem

    def read(self) -> GameSnapshot:
        m = self._mem
        return GameSnapshot(
            wall_time_utc=datetime.now(timezone.utc),
            global_timer=m.read_u32(A.GLOBAL_TIMER),
            mario_action=m.read_u32(A.MARIO_ACTION),
            mario_action_timer=m.read_u16(A.MARIO_ACTION_TIMER),
            num_stars=m.read_s16(A.MARIO_NUM_STARS),
            last_completed_course=m.read_s8(A.LAST_COMPLETED_COURSE),
            last_completed_star=m.read_s8(A.LAST_COMPLETED_STAR),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_snapshot.py -q`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/core/snapshot.py tests/test_snapshot.py
git commit -m "feat: GameSnapshot + SnapshotReader"
```

---

### Task 5: Event envelope and wire format

**Files:**
- Create: `src/sm64_events/core/events.py`
- Test: `tests/test_events.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_events.py
from datetime import datetime, timezone

from sm64_events.core.events import Event, to_wire


def test_wire_format():
    ts = datetime(2026, 6, 10, 22, 14, 3, 512000, tzinfo=timezone.utc)
    ev = Event(type="star_collected", frame=81234, timestamp_utc=ts,
               payload={"course_id": 1})
    wire = to_wire(ev, seq=412)
    assert wire == {
        "v": 1,
        "seq": 412,
        "type": "star_collected",
        "frame": 81234,
        "timestamp_utc": "2026-06-10T22:14:03.512000Z",
        "payload": {"course_id": 1},
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_events.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `events.py`**

```python
# src/sm64_events/core/events.py
"""Versioned event envelope shared by every event type."""
from dataclasses import dataclass
from datetime import datetime

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Event:
    type: str
    frame: int  # game-frame stamp (gGlobalTimer units, 30 Hz)
    timestamp_utc: datetime
    payload: dict


def to_wire(event: Event, seq: int) -> dict:
    return {
        "v": SCHEMA_VERSION,
        "seq": seq,
        "type": event.type,
        "frame": event.frame,
        "timestamp_utc": event.timestamp_utc.isoformat().replace("+00:00", "Z"),
        "payload": event.payload,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_events.py -q`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/core/events.py tests/test_events.py
git commit -m "feat: versioned event envelope with wire format"
```

---

### Task 6: Star-grab detector (the core of goal one)

**Files:**
- Create: `src/sm64_events/detectors/base.py`, `src/sm64_events/detectors/star_grab.py`
- Test: `tests/test_star_grab.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_star_grab.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.memory import addresses as A

ACT_IDLE = 0x0C400201  # any non-star-dance action works for tests


def snap(**overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
        global_timer=1000,
        mario_action=ACT_IDLE,
        mario_action_timer=0,
        num_stars=5,
        last_completed_course=1,
        last_completed_star=3,
    )
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def test_edge_into_star_dance_emits_identified_event():
    prev = snap(num_stars=5)
    curr = snap(mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=2,
                global_timer=1002, num_stars=6,
                last_completed_course=1, last_completed_star=3)
    events = StarGrabDetector().process(prev, curr)
    assert len(events) == 1
    ev = events[0]
    assert ev.type == "star_collected"
    assert ev.frame == 1000  # back-computed: 1002 - 2
    assert ev.payload == {
        "course_id": 1,
        "course_name": "Bob-omb Battlefield",
        "star_id": 2,  # game stores 1-based (3); API is 0-based
        "star_name": "Shoot to the Island in the Sky",
        "already_collected": False,
    }


def test_already_collected_star_still_fires_with_flag_true():
    prev = snap(num_stars=6)
    curr = snap(mario_action=A.ACT_STAR_DANCE_NO_EXIT, num_stars=6)
    events = StarGrabDetector().process(prev, curr)
    assert len(events) == 1
    assert events[0].payload["already_collected"] is True


def test_all_grab_action_variants_fire():
    for action in (A.ACT_STAR_DANCE_EXIT, A.ACT_STAR_DANCE_WATER,
                   A.ACT_STAR_DANCE_NO_EXIT, A.ACT_FALL_AFTER_STAR_GRAB):
        events = StarGrabDetector().process(snap(), snap(mario_action=action))
        assert len(events) == 1, hex(action)


def test_no_event_while_dance_continues():
    prev = snap(mario_action=A.ACT_STAR_DANCE_EXIT)
    curr = snap(mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=10)
    assert StarGrabDetector().process(prev, curr) == []


def test_no_event_on_fall_to_dance_transition():
    # midair grab: FALL_AFTER_STAR_GRAB already fired the event; the
    # follow-up dance action must not fire a second one
    prev = snap(mario_action=A.ACT_FALL_AFTER_STAR_GRAB)
    curr = snap(mario_action=A.ACT_STAR_DANCE_NO_EXIT)
    assert StarGrabDetector().process(prev, curr) == []


def test_no_event_without_edge():
    assert StarGrabDetector().process(snap(), snap(global_timer=1001)) == []


def test_same_star_twice_produces_two_events():
    d = StarGrabDetector()
    first = d.process(snap(), snap(mario_action=A.ACT_STAR_DANCE_EXIT))
    between = d.process(snap(mario_action=A.ACT_STAR_DANCE_EXIT), snap())
    second = d.process(snap(), snap(mario_action=A.ACT_STAR_DANCE_EXIT))
    assert len(first) == 1 and between == [] and len(second) == 1


def test_never_collected_sentinel_is_dropped():
    # last_completed_star == 0 means "never set" — cannot identify a star
    curr = snap(mario_action=A.ACT_STAR_DANCE_EXIT,
                last_completed_course=0, last_completed_star=0)
    assert StarGrabDetector().process(snap(), curr) == []


def test_frame_never_negative():
    curr = snap(mario_action=A.ACT_STAR_DANCE_EXIT,
                global_timer=1, mario_action_timer=5)
    events = StarGrabDetector().process(snap(), curr)
    assert events[0].frame == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_star_grab.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `base.py` and `star_grab.py`**

```python
# src/sm64_events/detectors/base.py
"""Detector contract: pure functions over snapshot pairs. No I/O, no clocks."""
from typing import Protocol

from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot


class Detector(Protocol):
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]: ...
```

```python
# src/sm64_events/detectors/star_grab.py
"""Emits star_collected on the edge into a star-grab action.

Why this works for re-collections: the game's interaction handler updates
gLastCompletedCourseNum/StarNum and Mario's numStars BEFORE setting the
star-dance action, on every grab. So at the edge, identity is already
current, and an unchanged numStars means the star was already owned.
"""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import STAR_GRAB_ACTIONS, course_name, star_name


class StarGrabDetector:
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        entered = (curr.mario_action in STAR_GRAB_ACTIONS
                   and prev.mario_action not in STAR_GRAB_ACTIONS)
        if not entered:
            return []
        star_id = curr.last_completed_star - 1  # game is 1-based, API 0-based
        if star_id < 0:
            return []
        course_id = curr.last_completed_course
        return [Event(
            type="star_collected",
            frame=max(0, curr.global_timer - curr.mario_action_timer),
            timestamp_utc=curr.wall_time_utc,
            payload={
                "course_id": course_id,
                "course_name": course_name(course_id),
                "star_id": star_id,
                "star_name": star_name(course_id, star_id),
                "already_collected": curr.num_stars == prev.num_stars,
            },
        )]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_star_grab.py -q`
Expected: 9 passed.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/detectors tests/test_star_grab.py
git commit -m "feat: star-grab detector with frame back-computation"
```

---

### Task 7: Game-reset detector

**Files:**
- Create: `src/sm64_events/detectors/lifecycle.py`
- Test: `tests/test_lifecycle.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_lifecycle.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.lifecycle import GameResetDetector


def snap(timer: int) -> GameSnapshot:
    return GameSnapshot(
        wall_time_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
        global_timer=timer, mario_action=0, mario_action_timer=0,
        num_stars=0, last_completed_course=0, last_completed_star=0,
    )


def test_backward_timer_jump_emits_game_reset():
    events = GameResetDetector().process(snap(5000), snap(100))
    assert len(events) == 1
    assert events[0].type == "game_reset"
    assert events[0].frame == 100


def test_forward_progress_is_silent():
    assert GameResetDetector().process(snap(100), snap(101)) == []


def test_paused_game_is_silent():
    assert GameResetDetector().process(snap(100), snap(100)) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lifecycle.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `lifecycle.py`**

```python
# src/sm64_events/detectors/lifecycle.py
"""game_reset: gGlobalTimer moved backward (console reset, savestate load
to an earlier point, ROM reload). Stats consumers use it to segment attempts."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot


class GameResetDetector:
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if curr.global_timer >= prev.global_timer:
            return []
        return [Event(type="game_reset", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc, payload={})]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lifecycle.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/detectors/lifecycle.py tests/test_lifecycle.py
git commit -m "feat: game_reset detector on backward timer jump"
```

---

### Task 8: PJ64 attach and RDRAM scan

**Files:**
- Create: `src/sm64_events/memory/pj64.py`
- Test: `tests/test_pj64_signature.py` (the pure signature check only; live behavior is covered by Task 12's harness)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pj64_signature.py
from sm64_events.memory import addresses as A
from sm64_events.memory.pj64 import looks_like_rdram


def fake_reader(values: dict):
    return lambda addr: values.get(addr, 0)


def test_accepts_n64_boot_config():
    reader = fake_reader({A.OS_ROM_BASE: 0xB0000000,
                          A.OS_MEM_SIZE: 0x400000,
                          A.OS_TV_TYPE: 1})
    assert looks_like_rdram(reader) is True


def test_accepts_expansion_pak_size():
    reader = fake_reader({A.OS_ROM_BASE: 0xB0000000,
                          A.OS_MEM_SIZE: 0x800000,
                          A.OS_TV_TYPE: 0})
    assert looks_like_rdram(reader) is True


def test_rejects_wrong_rom_base():
    reader = fake_reader({A.OS_ROM_BASE: 0x12345678,
                          A.OS_MEM_SIZE: 0x400000,
                          A.OS_TV_TYPE: 1})
    assert looks_like_rdram(reader) is False


def test_rejects_garbage_tv_type():
    reader = fake_reader({A.OS_ROM_BASE: 0xB0000000,
                          A.OS_MEM_SIZE: 0x400000,
                          A.OS_TV_TYPE: 7})
    assert looks_like_rdram(reader) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pj64_signature.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `pj64.py`**

```python
# src/sm64_events/memory/pj64.py
"""Attach to Project64 1.6 and locate the emulated N64 RDRAM.

Strategy: enumerate committed memory regions in the (32-bit) PJ64 process;
any region >= 4 MB whose start matches the libultra osBootConfig signature
is the RDRAM. Read-only access; never writes to the emulator.
"""
import ctypes
import logging
from collections.abc import Callable, Iterator
from ctypes import wintypes

import pymem
import pymem.exception

from sm64_events.memory import addresses as A
from sm64_events.memory.base import MemoryReadError, RdramReader

log = logging.getLogger("sm64.pj64")

PROCESS_NAME = "Project64.exe"
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100


class _MBI64(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_ulonglong),
        ("AllocationBase", ctypes.c_ulonglong),
        ("AllocationProtect", wintypes.DWORD),
        ("_align1", wintypes.DWORD),
        ("RegionSize", ctypes.c_ulonglong),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("_align2", wintypes.DWORD),
    ]


def iter_committed_regions(handle: int) -> Iterator[tuple[int, int]]:
    """Yield (base, size) of readable committed regions, low to high."""
    kernel32 = ctypes.windll.kernel32
    mbi = _MBI64()
    addr = 0
    while kernel32.VirtualQueryEx(handle, ctypes.c_void_p(addr),
                                  ctypes.byref(mbi), ctypes.sizeof(mbi)):
        readable = (mbi.State == MEM_COMMIT
                    and not (mbi.Protect & PAGE_GUARD)
                    and mbi.Protect != PAGE_NOACCESS)
        if readable:
            yield mbi.BaseAddress, mbi.RegionSize
        addr = mbi.BaseAddress + mbi.RegionSize
        if addr >= 0x1_0000_0000:  # PJ64 1.6 is 32-bit
            break


def looks_like_rdram(read_u32: Callable[[int], int]) -> bool:
    """osBootConfig signature — written by libultra in every N64 game."""
    return (read_u32(A.OS_ROM_BASE) == 0xB0000000
            and read_u32(A.OS_MEM_SIZE) in (0x400000, 0x800000)
            and read_u32(A.OS_TV_TYPE) <= 2)


class Pj64Memory(RdramReader):
    def __init__(self):
        self._pm: pymem.Pymem | None = None
        self._rdram_base: int | None = None

    @property
    def attached(self) -> bool:
        return self._pm is not None and self._rdram_base is not None

    def attach(self) -> bool:
        try:
            self._pm = pymem.Pymem(PROCESS_NAME)
        except pymem.exception.PymemError:
            self._pm = None
            return False
        for base, size in iter_committed_regions(self._pm.process_handle):
            if size < A.RDRAM_MIN_SIZE:
                continue
            if self._check_signature_at(base):
                self._rdram_base = base
                log.info("attached: RDRAM at host base 0x%X", base)
                return True
        self._pm = None  # process found, ROM not loaded yet
        return False

    def detach(self) -> None:
        self._pm = None
        self._rdram_base = None

    def _check_signature_at(self, base: int) -> bool:
        def u32(n64_addr: int) -> int:
            data = self._pm.read_bytes(base + (n64_addr - A.KSEG0_BASE), 4)
            return int.from_bytes(data, "little")
        try:
            return looks_like_rdram(u32)
        except pymem.exception.PymemError:
            return False

    def _read_raw(self, offset: int, size: int) -> bytes:
        if not self.attached:
            raise MemoryReadError("not attached to Project64")
        try:
            return self._pm.read_bytes(self._rdram_base + offset, size)
        except pymem.exception.PymemError as exc:
            raise MemoryReadError(str(exc)) from exc
```

Troubleshooting note for the live gate (Task 12): if `attach()` never finds RDRAM while the game is clearly running, PJ64 may have placed RDRAM at a non-zero offset inside a larger allocation. The fix is to also test candidate bases at 0x10000-byte steps inside regions ≥ 4 MB — add that inner loop only if the live test demands it (YAGNI until proven needed).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pj64_signature.py -q`
Expected: 4 passed.

(`pymem` imports fine without PJ64 running; nothing attaches during unit tests.)

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/memory/pj64.py tests/test_pj64_signature.py
git commit -m "feat: PJ64 process attach with osBootConfig RDRAM scan"
```

---

### Task 9: Broadcaster

**Files:**
- Create: `src/sm64_events/server/broadcaster.py`
- Test: `tests/test_broadcaster.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_broadcaster.py
import asyncio
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.server.broadcaster import Broadcaster


def make_event() -> Event:
    return Event(type="star_collected", frame=1,
                 timestamp_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
                 payload={})


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


class DeadWS:
    async def send_json(self, data):
        raise RuntimeError("client gone")


def test_publish_sends_to_all_clients_with_increasing_seq():
    b = Broadcaster()
    ws1, ws2 = FakeWS(), FakeWS()
    b.register(ws1)
    b.register(ws2)
    asyncio.run(b.publish(make_event()))
    asyncio.run(b.publish(make_event()))
    assert [m["seq"] for m in ws1.sent] == [1, 2]
    assert [m["seq"] for m in ws2.sent] == [1, 2]
    assert ws1.sent[0]["type"] == "star_collected"


def test_dead_client_is_dropped_without_blocking_others():
    b = Broadcaster()
    dead, alive = DeadWS(), FakeWS()
    b.register(dead)
    b.register(alive)
    asyncio.run(b.publish(make_event()))
    assert len(alive.sent) == 1
    assert b.client_count == 1


def test_unregister():
    b = Broadcaster()
    ws = FakeWS()
    b.register(ws)
    b.unregister(ws)
    asyncio.run(b.publish(make_event()))
    assert ws.sent == []
    assert b.client_count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_broadcaster.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `broadcaster.py`**

```python
# src/sm64_events/server/broadcaster.py
"""Fan one event stream out to every connected WebSocket client.

Owns the seq counter. A failing client is dropped, never retried, and never
blocks the poll loop or other clients.
"""
import json
import logging

from sm64_events.core.events import Event, to_wire

log = logging.getLogger("sm64.events")


class Broadcaster:
    def __init__(self):
        self._clients: set = set()
        self._seq = 0

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def register(self, ws) -> None:
        self._clients.add(ws)

    def unregister(self, ws) -> None:
        self._clients.discard(ws)

    async def publish(self, event: Event) -> None:
        self._seq += 1
        wire = to_wire(event, self._seq)
        log.info("event %s", json.dumps(wire))
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_json(wire)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_broadcaster.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/server/broadcaster.py tests/test_broadcaster.py
git commit -m "feat: WebSocket broadcaster with seq numbers and dead-client cleanup"
```

---

### Task 10: Poller

**Files:**
- Create: `src/sm64_events/server/poller.py`
- Test: `tests/test_poller.py`

The async `run()` loop is thin glue; the testable unit is `tick()`. The reader is injectable so tests script snapshot sequences without memory.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_poller.py
import asyncio
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.base import MemoryReadError
from sm64_events.server.poller import Poller


def snap(timer: int) -> GameSnapshot:
    return GameSnapshot(
        wall_time_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
        global_timer=timer, mario_action=0, mario_action_timer=0,
        num_stars=0, last_completed_course=0, last_completed_star=0,
    )


class StubMemory:
    attached = True

    def __init__(self):
        self.detached = False

    def detach(self):
        self.detached = True


class ScriptedReader:
    def __init__(self, snapshots):
        self._snaps = list(snapshots)

    def read(self):
        item = self._snaps.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class RecordingBroadcaster:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


class EchoDetector:
    """Emits one event per tick pair, tagged with both timers."""
    def process(self, prev, curr):
        return [Event(type="tick", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"prev": prev.global_timer})]


def test_first_tick_emits_nothing_then_detectors_run_on_pairs():
    b = RecordingBroadcaster()
    p = Poller(StubMemory(), [EchoDetector()], b,
               reader=ScriptedReader([snap(1), snap(2)]))
    asyncio.run(p.tick())
    assert b.events == []  # no prev yet
    asyncio.run(p.tick())
    assert len(b.events) == 1
    assert b.events[0].payload == {"prev": 1}
    assert p.latest.global_timer == 2


def test_read_error_detaches_and_emits_disconnected():
    mem = StubMemory()
    b = RecordingBroadcaster()
    p = Poller(mem, [EchoDetector()], b,
               reader=ScriptedReader([snap(1), MemoryReadError("gone")]))
    asyncio.run(p.tick())
    asyncio.run(p.tick())
    assert mem.detached is True
    assert [e.type for e in b.events] == ["emulator_disconnected"]
    assert p.latest is None


def test_no_stale_pair_after_reconnect():
    # after a disconnect, the next snapshot must NOT be paired with the
    # pre-disconnect one (savestate-style false edges)
    b = RecordingBroadcaster()
    p = Poller(StubMemory(), [EchoDetector()], b,
               reader=ScriptedReader([snap(1), MemoryReadError("gone"), snap(50)]))
    asyncio.run(p.tick())
    asyncio.run(p.tick())
    asyncio.run(p.tick())
    tick_events = [e for e in b.events if e.type == "tick"]
    assert tick_events == []  # snap(50) had no prev


def test_implausible_snapshot_means_layout_mismatch_and_refusal():
    # spec: never silently emit wrong star IDs — an impossible value means
    # the address layout doesn't match; detach and emit nothing
    mem = StubMemory()
    b = RecordingBroadcaster()
    bad = GameSnapshot(
        wall_time_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
        global_timer=1, mario_action=0, mario_action_timer=0,
        num_stars=29999, last_completed_course=0, last_completed_star=0,
    )
    p = Poller(mem, [EchoDetector()], b, reader=ScriptedReader([bad]))
    asyncio.run(p.tick())
    assert mem.detached is True
    assert b.events == []
    assert p.latest is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_poller.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `poller.py`**

```python
# src/sm64_events/server/poller.py
"""60 Hz poll loop: snapshot -> detectors -> broadcast.

Polling at ~60 Hz against 30 Hz game logic means every game frame is
observed; the star dance lasts ~60-90 frames so edges cannot be missed.
"""
import asyncio
import logging
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot, SnapshotReader
from sm64_events.memory.base import MemoryReadError

log = logging.getLogger("sm64.poller")


def _lifecycle_event(type_: str) -> Event:
    return Event(type=type_, frame=0,
                 timestamp_utc=datetime.now(timezone.utc), payload={})


def _plausible(snap: GameSnapshot) -> bool:
    """Layout sanity: values the real game can never produce mean the
    address registry doesn't match this ROM — refuse rather than emit
    wrong star IDs (spec: hard refusal on layout mismatch)."""
    return (0 <= snap.num_stars <= 182
            and 0 <= snap.last_completed_course <= 25
            and 0 <= snap.last_completed_star <= 7)


class Poller:
    def __init__(self, memory, detectors, broadcaster, hz: int = 60, reader=None):
        self.memory = memory
        self.detectors = list(detectors)
        self.broadcaster = broadcaster
        self.interval = 1.0 / hz
        self.reader = reader or SnapshotReader(memory)
        self.latest: GameSnapshot | None = None
        self._prev: GameSnapshot | None = None

    async def tick(self) -> None:
        try:
            curr = self.reader.read()
        except MemoryReadError:
            log.warning("lost emulator; detaching")
            self.memory.detach()
            self._prev = None
            self.latest = None
            await self.broadcaster.publish(_lifecycle_event("emulator_disconnected"))
            return
        if not _plausible(curr):
            log.error("memory layout mismatch (impossible values read) — "
                      "refusing to emit events; check the address registry")
            self.memory.detach()
            self._prev = None
            self.latest = None
            return
        if self._prev is not None:
            for detector in self.detectors:
                for event in detector.process(self._prev, curr):
                    await self.broadcaster.publish(event)
        self._prev = curr
        self.latest = curr

    async def run(self) -> None:
        while True:
            if not self.memory.attached:
                if self.memory.attach():
                    await self.broadcaster.publish(_lifecycle_event("emulator_connected"))
                else:
                    await asyncio.sleep(2.0)
                    continue
            await self.tick()
            await asyncio.sleep(self.interval)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_poller.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/server/poller.py tests/test_poller.py
git commit -m "feat: poll loop with attach retry, layout-mismatch refusal, coherent pairs"
```

---

### Task 11: FastAPI app, logging, and wiring

**Files:**
- Create: `src/sm64_events/server/app.py`, `src/sm64_events/core/logging_setup.py`, `src/sm64_events/main.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_app.py
from fastapi.testclient import TestClient

from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller


class OfflineMemory:
    """Never attaches — keeps the poll loop idling during endpoint tests."""
    attached = False

    def attach(self):
        return False

    def detach(self):
        pass


def make_client() -> TestClient:
    broadcaster = Broadcaster()
    poller = Poller(OfflineMemory(), [StarGrabDetector()], broadcaster)
    app = create_app(poller, broadcaster, debug_hooks=True)
    return TestClient(app)


def test_health_reports_unattached():
    with make_client() as client:
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["emulator_attached"] is False
        assert body["clients"] == 0
        assert body["last_frame"] is None


def test_state_is_null_before_first_snapshot():
    with make_client() as client:
        assert client.get("/state").json() == {"snapshot": None}


def test_websocket_receives_published_events():
    with make_client() as client:
        with client.websocket_connect("/ws/events") as ws:
            client.post("/debug/emit")
            msg = ws.receive_json()
            assert msg["v"] == 1
            assert msg["seq"] == 1
            assert msg["type"] == "debug"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `logging_setup.py`**

```python
# src/sm64_events/core/logging_setup.py
"""Persistent file logging (UTC) plus console output."""
import logging
import time
from pathlib import Path


def configure_logging(log_dir: Path = Path("logs")) -> None:
    log_dir.mkdir(exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (logging.FileHandler(log_dir / "sm64_events.log", encoding="utf-8"),
                    logging.StreamHandler()):
        handler.setFormatter(formatter)
        root.addHandler(handler)
```

- [ ] **Step 4: Write `app.py`**

```python
# src/sm64_events/server/app.py
"""HTTP/WebSocket surface: /ws/events (broadcast), /health, /state."""
import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from sm64_events.core.events import Event
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller


def create_app(poller: Poller, broadcaster: Broadcaster,
               debug_hooks: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(poller.run())
        yield
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    app = FastAPI(title="SM64 Event API", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "emulator_attached": poller.memory.attached,
            "clients": broadcaster.client_count,
            "last_frame": poller.latest.global_timer if poller.latest else None,
        }

    @app.get("/state")
    def state():
        if poller.latest is None:
            return {"snapshot": None}
        d = asdict(poller.latest)
        d["wall_time_utc"] = poller.latest.wall_time_utc.isoformat().replace("+00:00", "Z")
        return {"snapshot": d}

    @app.websocket("/ws/events")
    async def ws_events(websocket: WebSocket):
        await websocket.accept()
        broadcaster.register(websocket)
        try:
            while True:
                await websocket.receive_text()  # ignore input; detect disconnect
        except WebSocketDisconnect:
            pass
        finally:
            broadcaster.unregister(websocket)

    if debug_hooks:
        @app.post("/debug/emit")
        async def debug_emit():
            await broadcaster.publish(Event(
                type="debug", frame=0,
                timestamp_utc=datetime.now(timezone.utc), payload={}))
            return {"ok": True}

    return app
```

- [ ] **Step 5: Write `main.py`**

```python
# src/sm64_events/main.py
"""Composition root: wire registry -> memory -> poller -> detectors -> app."""
from sm64_events.core.logging_setup import configure_logging
from sm64_events.detectors.lifecycle import GameResetDetector
from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.memory.pj64 import Pj64Memory
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller


def build():
    configure_logging()
    memory = Pj64Memory()
    broadcaster = Broadcaster()
    poller = Poller(memory, [GameResetDetector(), StarGrabDetector()], broadcaster)
    return create_app(poller, broadcaster)


app = build()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8064)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_app.py -q`
Expected: 3 passed.

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest -q`
Expected: all tests pass (38 at this point).

- [ ] **Step 8: Commit**

```bash
git add src/sm64_events/server/app.py src/sm64_events/core/logging_setup.py src/sm64_events/main.py tests/test_app.py
git commit -m "feat: FastAPI app with ws/events, health, state + wiring"
```

---

### Task 12: Live verification harness and manual gate

**Files:**
- Create: `tools/verify_addresses.py`

This is the gate for every `VERIFY` entry in the registry, and the manual acceptance test for goal one. It runs against the real emulator — no unit tests.

- [ ] **Step 1: Write the harness**

```python
# tools/verify_addresses.py
"""Live verification of registry addresses against PJ64 1.6 + Usamune v1.93u.

Usage: start PJ64 1.6 with Usamune running (UNPAUSED, in-game), then:

    uv run python tools/verify_addresses.py

Phase 1 runs automatic checks (PASS/FAIL per address). Phase 2 is a live
watch: grab stars and confirm the printed identity matches what you grabbed.
On any FAIL, cross-check the address at ukikipedia.net/wiki/RAM (US column)
or the SM64 decomp US symbol map, fix addresses.py, and rerun.
"""
import time

from sm64_events.core.snapshot import SnapshotReader
from sm64_events.memory import addresses as A
from sm64_events.memory.pj64 import Pj64Memory


def check(label: str, ok: bool, detail: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {detail}")
    return ok


def main() -> None:
    mem = Pj64Memory()
    print("Attaching to Project64.exe ...")
    while not mem.attach():
        print("  not found (is PJ64 running with the ROM loaded?) retrying in 2s")
        time.sleep(2)
    print("Attached.\n\nPhase 1: automatic checks (game must be unpaused)")

    reader = SnapshotReader(mem)
    s1 = reader.read()
    time.sleep(1.0)
    s2 = reader.read()

    delta = s2.global_timer - s1.global_timer
    ok = True
    ok &= check("GLOBAL_TIMER ticks ~30/s", 25 <= delta <= 35,
                f"delta over 1s = {delta}")
    ok &= check("MARIO_NUM_STARS plausible", 0 <= s2.num_stars <= 182,
                f"numStars = {s2.num_stars} (compare with the in-game counter)")
    ok &= check("MARIO_ACTION nonzero", s2.mario_action != 0,
                f"action = {s2.mario_action:#010x}")
    ok &= check("LAST_COMPLETED plausible",
                0 <= s2.last_completed_course <= 25
                and 0 <= s2.last_completed_star <= 7,
                f"course={s2.last_completed_course} star={s2.last_completed_star}")
    print("\nPhase 1:", "ALL PASS" if ok else "FAILURES — fix addresses.py first")

    print("\nPhase 2: live watch — grab stars and verify identity/timing.")
    print("(Ctrl+C to quit)\n")
    prev_action = None
    while True:
        s = reader.read()
        if s.mario_action != prev_action:
            in_set = s.mario_action in A.STAR_GRAB_ACTIONS
            star_id = s.last_completed_star - 1
            tag = (f"  << STAR GRAB: {A.course_name(s.last_completed_course)} / "
                   f"{A.star_name(s.last_completed_course, star_id)} "
                   f"(grab frame {s.global_timer - s.mario_action_timer})"
                   if in_set else "")
            print(f"frame {s.global_timer:>8}  action {s.mario_action:#010x}  "
                  f"stars {s.num_stars:>3}{tag}")
            prev_action = s.mario_action
        time.sleep(0.016)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add tools/verify_addresses.py
git commit -m "feat: live address verification harness"
```

- [ ] **Step 3: Run the manual live gate (requires the human at the keyboard)**

Start PJ64 1.6 with Usamune v1.93u, enter a save file, then run:
`uv run python tools/verify_addresses.py`

Checklist — every item must pass before goal one is closed:

1. Phase 1 prints ALL PASS. If `GLOBAL_TIMER` fails, the game was paused or the address is wrong — unpause and rerun first.
2. Grab a **new** star (ground grab): harness prints the correct course/star name; `stars` count increments.
3. Grab an **already-owned** star: correct identity prints; `stars` count does NOT increment.
4. Grab the **same star twice in a row** (re-enter the level between grabs): two STAR GRAB lines.
5. Grab a star **in midair**: a STAR GRAB line appears at the moment of the touch. If instead you only see an unknown `0x000019xx` action at touch time followed by the dance, update `ACT_FALL_AFTER_STAR_GRAB` in `addresses.py` to the observed value and rerun.
6. Grab a star **underwater** (e.g., JRB): STAR GRAB line with correct identity.
7. Grab the same star several times: the printed *grab frame* minus your savestate's load frame should be consistent run-to-run (validates `actionTimer` back-computation). If grab frames look wrong, note the raw values and investigate before trusting timing.
8. **Server end-to-end:** run `uv run uvicorn sm64_events.main:app --host 127.0.0.1 --port 8064`, connect with a WebSocket client (e.g., browser console: `new WebSocket("ws://127.0.0.1:8064/ws/events").onmessage = e => console.log(e.data)`), grab a star, and confirm the `star_collected` JSON arrives with correct identity. Check `/health` while attached and after closing PJ64 (expect `emulator_attached` to flip and an `emulator_disconnected` event).
9. Load a savestate mid-star-dance: no spurious event.
10. Remove the `VERIFY` comments from entries that passed; commit:

```bash
git add src/sm64_events/memory/addresses.py
git commit -m "chore: live-verified registry addresses against Usamune v1.93u"
```

---

## Execution order and dependencies

Tasks 1→2→3→4→5 are strictly sequential (each builds on the previous). Tasks 6 and 7 depend on 4+5 and are independent of each other. Task 8 depends on 2+3. Tasks 9→10→11 are sequential and need 5 (and 8 for main.py wiring). Task 12 needs everything.

## Out of scope (per spec)

Stats computation, key/grand-star event types, tracker overlay, non-PJ64-1.6 emulators, writing to game memory.
