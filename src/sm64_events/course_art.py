"""Course portraits, cut from the player's own ROM when the app runs.

The Trainer ships no Nintendo pictures. Griffin, 2026-11-09: "The point is that
anything we do leverages the user's existing ROM". Each course portrait is the
game's own picture of that course -- the castle painting you jump into, or a
texture of the course itself -- decoded from the Usamune ROM the player already
has (interface I3: `scenelab.roms.rom_for(region)`) and kept in a cache in the
data folder, one folder per ROM, named by the ROM's hash. Nothing here writes a
pixel anywhere else, and nothing here sends one off this computer.

Without a ROM the listing names a plain stand-in for every portrait: the
course's short name on a tile in the page's own colours (`standin_svg`), drawn
by us, the same square, so no layout moves when the real picture arrives.

Where the pictures are (decomp n64decomp/sm64 at 06ec56df, read 2026-11-09):

- The castle's paintings are RGBA16 halves of 64x32 in the castle interior's
  segment-7 block (`levels/castle_inside/painting.inc.c`: each painting's
  `textures[]` is {bottom, top}; `texture.inc.c` gives the offsets). The JRB
  painting differs by region (`23_us`/`24_us`: the US ship, the JP bubbles), so
  JRB pins two digests. The SSL and SL "paintings" are plain walls and HMC's
  and DDD's are water ripples, so those four keep their star stand-ins
  (ui/entities.js COURSE_SUBSTITUTE_ICONS).
- Peach's portrait (the Princess's Secret Slide's picture) and the Bowser
  portrait it turns into are four 32x32 tiles each, castle segment 7
  0x6000-0x7800 and 0x8000-0x9800.
- Big Boo's Haunt has no painting: its picture is the 32x32 Boo portrait on
  the mansion's wall, BBH segment 7 offset 0x1000.
- Rainbow Ride and Tick Tock Clock keep the pictures the Trainer already
  shipped (`SHIPPED`): screenshots Griffin took in the game himself, not the
  game's own files. Griffin, 2026-11-09: "we should use the icons we already
  have. Those are screenshots I personally took from in-game." (RR has no
  painting; TTC's painting in the ROM has no clock hands.)

A block is found by content, not by address: the MIO0 block whose decoded size
is the block's, checked by the sha256 of every portrait's texture bytes (the
digests below, measured on Usamune v1.93u and v1.93j). Those digests are
fingerprints, not pictures. A ROM whose bytes do not match gives the stand-in
for that portrait, never a wrong picture.

Measured 2026-11-09 on his two ROMs (read-only, into a scratch cache): all 13
portraits from each; `extract` takes ~40 ms, a first `refresh` 50-180 ms with
the 8 MiB read and hash, a cached ROM ~10 ms. The 13 PNGs are ~130 KB together.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import shutil
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("sm64.course_art")

#: Bumped when a recipe or the output changes, so an old cache is rebuilt.
FORMAT_VERSION = 1
#: Every portrait is written square at this many pixels, upscaled by whole
#: pixels (nearest neighbour). The shipped art was the same textures at ~620px;
#: the picker draws ~104px cells and the banner up to ~174px on 3x screens.
OUTPUT_PIXELS = 512
REGIONS = ("us", "jp")          # which ROM supplies the pictures, first found wins
WATCH_SECONDS = 15.0            # how often the watcher asks for a ROM while it has none
MIO0_LIMIT = 4 << 20

# The page's own colours (ui/index.html :root), copied because an <img> SVG
# cannot read CSS variables. tests/test_course_art.py compares them.
STANDIN_COLOURS = {"--surface-3": "#14263a", "--border": "#294058", "--text": "#eef2f6",
                   "--muted": "#8fa0b5"}
STANDIN_FONT = '"Segoe UI Variable", "Aptos", "Segoe UI", sans-serif'


@dataclass(frozen=True)
class Tile:
    """One RGBA16 texture inside a block, and where it lands on the portrait."""
    offset: int
    width: int
    height: int
    x: int
    y: int
    mirror: bool = False        # left-right
    flip: bool = False          # top-bottom


@dataclass(frozen=True)
class Portrait:
    stem: str                   # the stem ui/entities.js asks for
    label: str                  # the stand-in's text
    block: str                  # which ROM block holds the tiles (BLOCKS)
    size: int                   # the portrait's own pixels, square
    tiles: tuple[Tile, ...]
    digests: frozenset[str] = field(default_factory=frozenset)   # sha256 of the tiles' raw bytes


#: Decoded sizes of the MIO0 blocks the portraits read, as measured on Usamune v1.93u and v1.93j (RR's differs by
#: region). A hint for which blocks to try first; a ROM where none matches is searched block by block.
BLOCKS = {"castle": frozenset({0x79118}), "bbh": frozenset({0x26E44}), "rr": frozenset({0x2EE76, 0x2F032})}


def _painting(top: int, bottom: int) -> tuple[Tile, ...]:
    return (Tile(top, 64, 32, 0, 0), Tile(bottom, 64, 32, 0, 32))


def _quad(*offsets: int) -> tuple[Tile, ...]:
    return tuple(Tile(o, 32, 32, (i % 2) * 32, (i // 2) * 32) for i, o in enumerate(offsets))


def _mirrored_quarter(offset: int) -> tuple[Tile, ...]:
    return (Tile(offset, 32, 32, 0, 0), Tile(offset, 32, 32, 32, 0, mirror=True),
            Tile(offset, 32, 32, 0, 32, flip=True), Tile(offset, 32, 32, 32, 32, mirror=True, flip=True))


def _d(*hexes: str) -> frozenset[str]:
    return frozenset(hexes)


PORTRAITS: tuple[Portrait, ...] = (
    Portrait("bob", "BoB", "castle", 64, _painting(0xA800, 0xB800), _d(
        "69a232d9c837c899e14057f4bf06af5a65a7ec1b8d98de830a887c2b6e7e75d1")),
    Portrait("wf", "WF", "castle", 64, _painting(0xE800, 0xF800), _d(
        "ddf4f0ca906036fda11430ae5073648895864816b4a27df5eaae7b51c666bece")),
    Portrait("jrb", "JRB", "castle", 64, _painting(0x10800, 0x11800), _d(
        "72af65513bcd3b5dbaf1574bdbf2ad1a3fd625b69663d9bf584ed1394e91673d",     # US: the sunken ship
        "b7cb7d404e34120708250775397788f2424de62d7ea00a600eedb6eb21abb782")),   # JP: bubbles
    Portrait("ccm", "CCM", "castle", 64, _painting(0xC800, 0xD800), _d(
        "211b1b0cdb4e70d986ac325fa1ef482efe305c2afb26b5ca8f6bdb0df592c790")),
    Portrait("bbh", "BBH", "bbh", 32, (Tile(0x1000, 32, 32, 0, 0),), _d(
        "b617d418799c704d4422bc9bf377702404c42f3ffad124e2b2ea9d813f09a474")),
    Portrait("lll", "LLL", "castle", 64, _painting(0x13800, 0x12800), _d(
        "56dacd335f66713f622f7c21458be0865e1f6c721f423c99b97a6ae42eedef96")),
    Portrait("wdw", "WDW", "castle", 64, _painting(0x17800, 0x18800), _d(
        "0734f88da00ad5e3d1d09f437baa0078571b215d7292c6b723cb52115ea1b8f0")),
    Portrait("ttm", "TTM", "castle", 64, _painting(0x1B800, 0x1C800), _d(
        "6ef93ed5f54a6de6b4bfb981f8f8532092e7f30206f1ed07c1a9a6a2171cb18c")),
    Portrait("thi", "THI", "castle", 64, _painting(0x19800, 0x1A800), _d(
        "bc8317c7c0aa51b4f4230a296d7497c874effdfc7f23debea415f02f5069d53a")),
    Portrait("bitfs", "BitFS", "castle", 64, _quad(0x8000, 0x8800, 0x9000, 0x9800), _d(
        "d311d2eb4af7620984d4f9d804fb874f83dbb167a0605332e677f0499a0b442d")),
    Portrait("princess_secret", "PSS", "castle", 64, _quad(0x6000, 0x6800, 0x7000, 0x7800), _d(
        "a98197f0466d39697883ab187ac564ba5c79f21a959d283d0a107ed2555da13d")),
)
PORTRAITS_BY_STEM = {p.stem: p for p in PORTRAITS}
#: Course portraits the Trainer ships itself, by stem: Griffin's own in-game screenshots (ui/assets/course_icons),
#: served as they are instead of a cut from the ROM. tests/test_course_art.py pins their bytes.
SHIPPED = {"rr": "rr.webp", "ttc": "ttc.webp"}
SHIPPED_KEY = "shipped"
SHIPPED_DIR = Path(__file__).resolve().parent / "ui" / "assets" / "course_icons"


def shipped_file(name: str) -> Path | None:
    """One of SHIPPED's pictures by file name, or None for any other name."""
    if name not in SHIPPED.values():
        return None
    path = SHIPPED_DIR / name
    return path if path.is_file() else None


# -- decoding ----------------------------------------------------------------------------------------------------

def mio0(rom: bytes, at: int, limit: int | None = None) -> bytes:
    """Decode the MIO0 block at `at`, stopping after `limit` bytes when given. Raises ValueError on anything
    that is not a well-formed block."""
    if rom[at:at + 4] != b"MIO0":
        raise ValueError("not a MIO0 block")
    size, comp, raw = struct.unpack_from(">III", rom, at + 4)
    if size > MIO0_LIMIT:
        raise ValueError("MIO0 block too large")
    want = size if limit is None else min(size, limit)
    out = bytearray()
    flags, ci, ri = at + 16, at + comp, at + raw
    try:
        while len(out) < want:
            word = int.from_bytes(rom[flags:flags + 4], "big")
            flags += 4
            for bit in range(31, -1, -1):
                if len(out) >= want:
                    break
                if (word >> bit) & 1:
                    out.append(rom[ri])
                    ri += 1
                    continue
                pair = (rom[ci] << 8) | rom[ci + 1]
                ci += 2
                count, back = (pair >> 12) + 3, (pair & 0xFFF) + 1
                start = len(out) - back
                if start < 0:
                    raise ValueError("MIO0 reference before the start")
                if back >= count:
                    out += out[start:start + count]
                else:
                    for _ in range(count):
                        out.append(out[-back])
    except IndexError:
        raise ValueError("MIO0 block runs past the ROM") from None
    return bytes(out[:want])


def rgba16(data: bytes, width: int, height: int):
    """An RGBA16 (5-5-5-1, big-endian) texture as a Pillow image."""
    import numpy as np
    from PIL import Image
    words = np.frombuffer(data[:width * height * 2], dtype=">u2").reshape(height, width)
    channels = [((words >> shift) & 31) * 255 // 31 for shift in (11, 6, 1)]
    alpha = (words & 1) * 255
    return Image.fromarray(np.dstack(channels + [alpha]).astype(np.uint8), "RGBA")


def mio0_blocks(rom: bytes) -> list[tuple[int, int]]:
    """(ROM offset, decoded size) of every MIO0 header in the ROM. On his ROMs: 79 blocks, under a millisecond."""
    blocks = []
    at = rom.find(b"MIO0")
    while at != -1:
        if at + 16 <= len(rom):
            blocks.append((at, struct.unpack_from(">I", rom, at + 4)[0]))
        at = rom.find(b"MIO0", at + 4)
    return blocks


def _tile_bytes(block: bytes, portrait: Portrait) -> bytes:
    return b"".join(block[t.offset:t.offset + t.width * t.height * 2] for t in portrait.tiles)


def _reach(portrait: Portrait) -> int:
    return max(t.offset + t.width * t.height * 2 for t in portrait.tiles)


def _compose(block: bytes, portrait: Portrait):
    from PIL import Image, ImageOps
    canvas = Image.new("RGBA", (portrait.size, portrait.size))
    for tile in portrait.tiles:
        image = rgba16(block[tile.offset:tile.offset + tile.width * tile.height * 2], tile.width, tile.height)
        if tile.mirror:
            image = ImageOps.mirror(image)
        if tile.flip:
            image = ImageOps.flip(image)
        canvas.paste(image, (tile.x, tile.y))
    scale = max(1, OUTPUT_PIXELS // portrait.size)
    return canvas.resize((portrait.size * scale, portrait.size * scale), Image.NEAREST)


def extract(rom: bytes, portraits: tuple[Portrait, ...] = PORTRAITS) -> dict[str, bytes]:
    """stem -> PNG bytes for every portrait whose texture bytes match a pinned digest. A portrait no block
    matches is left out (its stand-in shows).

    Blocks of a known size are tried first (both Usamune ROMs resolve there); whatever is still missing is
    then looked for in every other block, nearest size first."""
    blocks = mio0_blocks(rom)
    pending = list(portraits)
    pictures: dict[str, bytes] = {}

    def attempt(at: int, wanted: list[Portrait]) -> None:
        try:
            block = mio0(rom, at, max(_reach(p) for p in wanted))
        except ValueError:
            return
        for portrait in wanted:
            if portrait in pending and len(block) >= _reach(portrait) and \
                    hashlib.sha256(_tile_bytes(block, portrait)).hexdigest() in portrait.digests:
                buffer = io.BytesIO()
                _compose(block, portrait).save(buffer, "PNG")
                pictures[portrait.stem] = buffer.getvalue()
                pending.remove(portrait)

    for name, sizes in BLOCKS.items():
        for at, size in blocks:
            wanted = [p for p in pending if p.block == name]
            if wanted and size in sizes:
                attempt(at, wanted)
    for name, sizes in BLOCKS.items():
        hint = min(sizes)
        for at, size in sorted(blocks, key=lambda b: abs(b[1] - hint)):
            wanted = [p for p in pending if p.block == name]
            if not wanted:
                break
            if size not in sizes:
                attempt(at, wanted)
    for portrait in pending:
        log.info("course art: no %s picture in this ROM matches the known bytes", portrait.stem)
    return pictures


# -- the stand-in --------------------------------------------------------------------------------------------------

def standin_svg(stem: str) -> str | None:
    """Our own plain tile for a portrait the app has no ROM picture for: the course's short name, in the page's
    colours. None for a stem that is not a portrait."""
    portrait = PORTRAITS_BY_STEM.get(stem)
    if portrait is None:
        return None
    c = STANDIN_COLOURS
    label = portrait.label
    font_size = 44 if len(label) <= 3 else 34
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">'
        f'<rect x="1" y="1" width="126" height="126" rx="14" fill="{c["--surface-3"]}" '
        f'stroke="{c["--border"]}" stroke-width="2"/>'
        f'<text x="64" y="64" text-anchor="middle" dominant-baseline="central" '
        f"font-family='{STANDIN_FONT}' font-weight="
        f'"700" font-size="{font_size}" fill="{c["--text"]}">{label}</text>'
        "</svg>")


# -- the cache and the watcher -----------------------------------------------------------------------------------

_KEY = re.compile(r"^[0-9a-f]{16}-v\d+$")
_NAME = re.compile(r"^[a-z_]+\.png$")
STANDIN = "standin"


def default_rom_for(region: str) -> Path | None:
    """Interface I3 (node P5's ROM store). A checkout without the store has no ROM."""
    try:
        from sm64_events.scenelab.roms import rom_for
    except ModuleNotFoundError as error:
        if error.name and error.name.startswith("sm64_events.scenelab"):
            return None
        raise
    return rom_for(region)


def shipped_listing() -> dict[str, str]:
    """SHIPPED's URL tails, which every listing carries, with or without a ROM."""
    return {stem: f"{SHIPPED_KEY}/{name}" for stem, name in SHIPPED.items() if shipped_file(name) is not None}


class CourseArt:
    """One cache folder of portraits, filled from whichever ROM `rom_for` names. Safe from any thread."""

    def __init__(self, root: Path, rom_for: Callable[[str], Path | None] = default_rom_for, *,
                 portraits: tuple[Portrait, ...] = PORTRAITS, watch_seconds: float = WATCH_SECONDS):
        self.root = Path(root)
        self._rom_for = rom_for
        self._portraits = portraits
        self._watch_seconds = watch_seconds
        self._lock = threading.RLock()                             # the listing's state
        self._refreshing = threading.Lock()                        # one refresh at a time
        self._rom_stamp: tuple[str, int, int] | None = None      # (path, size, mtime) of the ROM last read
        self._key: str | None = None
        self._stems: tuple[str, ...] = ()
        self._region: str | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_seconds: float | None = None                    # how long the last extraction took

    # -- what the server asks --------------------------------------------------------------------------------

    def listing(self) -> dict:
        """Every portrait's URL tail under /api/icons/course/: the ROM picture where there is one, else the
        stand-in. Never waits on an extraction."""
        with self._lock:
            key, stems, region = self._key, set(self._stems), self._region
        courses = {p.stem: f"{key}/{p.stem}.png" if p.stem in stems else f"{STANDIN}/{p.stem}.svg"
                   for p in self._portraits}
        courses |= shipped_listing()
        return {"courses": courses, "from_rom": bool(stems), "region": region if stems else None}

    def file(self, key: str, name: str) -> Path | None:
        """The cached picture `key/name`, or None. Names are checked, so no path leaves the cache."""
        if not _KEY.match(key) or not _NAME.match(name):
            return None
        path = self.root / key / name
        return path if path.is_file() else None

    # -- finding and cutting ------------------------------------------------------------------------------

    def refresh(self) -> bool:
        """Find the ROM and make sure its pictures are cached. Returns True when the listing changed. The slow
        part runs outside the state lock, so a listing asked meanwhile answers at once with what it had."""
        with self._refreshing:
            found = self._find_rom()
            if found is None:
                return False
            region, path = found
            try:
                stat = path.stat()
            except OSError:
                return False
            stamp = (str(path), stat.st_size, stat.st_mtime_ns)
            if stamp == self._rom_stamp:
                return False
            started = time.perf_counter()
            rom = path.read_bytes()
            key = f"{hashlib.sha256(rom).hexdigest()[:16]}-v{FORMAT_VERSION}"
            stems = self._cached(key) or self._cut(rom, key)
            self.last_seconds = time.perf_counter() - started
            self._rom_stamp = stamp
            with self._lock:
                changed = (key, stems) != (self._key, self._stems)
                self._key, self._stems, self._region = key, stems, region
            if changed:
                log.info("course art: %d of %d portraits from the %s ROM (%.0f ms)", len(stems),
                         len(self._portraits), region, self.last_seconds * 1000)
            return changed

    def _find_rom(self) -> tuple[str, Path] | None:
        for region in REGIONS:
            try:
                path = self._rom_for(region)
            except Exception:      # the store is someone else's; a failure there is "no ROM yet"
                log.exception("course art: asking for the %s ROM failed", region)
                path = None
            if path is not None:
                return region, Path(path)
        return None

    def _cached(self, key: str) -> tuple[str, ...] | None:
        try:
            record = json.loads((self.root / key / "portraits.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        stems = record.get("stems") if isinstance(record, dict) else None
        if record.get("version") != FORMAT_VERSION or not isinstance(stems, list):
            return None
        if not all(isinstance(s, str) and (self.root / key / f"{s}.png").is_file() for s in stems):
            return None
        return tuple(sorted(stems))

    def _cut(self, rom: bytes, key: str) -> tuple[str, ...]:
        pictures = extract(rom, self._portraits)
        self.root.mkdir(parents=True, exist_ok=True)
        staging = self.root / f".tmp-{os.getpid()}-{threading.get_ident()}-{key}"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        try:
            for stem, png in pictures.items():
                (staging / f"{stem}.png").write_bytes(png)
            record = {"version": FORMAT_VERSION, "stems": sorted(pictures),
                      "made_utc": datetime.now(UTC).isoformat(timespec="seconds")}
            (staging / "portraits.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
            target = self.root / key
            shutil.rmtree(target, ignore_errors=True)
            os.replace(staging, target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        self._forget_others(key)
        return tuple(sorted(pictures))

    def _forget_others(self, key: str) -> None:
        """Older ROMs' folders: one ROM's pictures are all the cache keeps."""
        for entry in self.root.iterdir():
            if entry.is_dir() and _KEY.match(entry.name) and entry.name != key:
                shutil.rmtree(entry, ignore_errors=True)

    # -- the watcher ------------------------------------------------------------------------------------------

    def start(self, on_change: Callable[[], None]) -> None:
        """Watch for the ROM in the background: once now, then every `watch_seconds`. `on_change` runs (on the
        watcher's thread) whenever the listing changes. Idempotent."""
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._watch, args=(on_change,), name="course-art",
                                            daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _watch(self, on_change: Callable[[], None]) -> None:
        while not self._stop.is_set():
            try:
                if self.refresh():
                    on_change()
            except Exception:
                log.exception("course art: refresh failed")
            self._stop.wait(self._watch_seconds)
