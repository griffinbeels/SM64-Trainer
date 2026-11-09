"""A ghost's start frame: the game state its recording began from, kept beside the ghost so the scene lab
can re-simulate the run exactly from the ghost's own pads after the attempt's scene is gone, and carried
with a ghost upload so a friend's lab can do the same.

The two doors the community client calls (community/ghost_start.py), frozen 2026-11-08:
    start_blob(root, clip_sha256) -> bytes | None          his own start of that ghost, as it travels
    accept_start_blob(root, clip_bytes, blob) -> Path       a downloaded one: checked, bound to the ghost, kept apart

Kept apart from the clip on purpose: the clip format is what the race exchanges, and a start is game state.
One file per clip, named by the clip's sha256, under the ghost population's folder: `starts/<sha256>.sm64start`
for his own, `starts/downloaded/<sha256>.sm64start` for anyone else's. A ghost without one still reads and
still re-simulates (tools/scene_lab/ghost_resim.py).

A downloaded start is UNTRUSTED: it becomes game state in the simulation and, through a save state, in a slot
of his real game, so a crafted one could aim object fields at bytes laid out as behaviour commands. This
module checks what it can without the ROM: a size bound before and during decompression, the version, the
game version, regions that are exactly the scene tap's spans for that game version (nothing outside them is
ever written, no code, no stacks), main-pool runs inside the main pool, and a binding to its ghost (the
regions' clocks, level, area and Mario must equal the ghost's first record). The lab then checks every
pointer against the ROM build before simulating anything (ghost_resim.check_untrusted); a start that fails
either check is refused with a plain sentence and never used.

Format v3 (little-endian): magic SM64STRT, u32 version, u32 JSON length, the JSON, then xz of the payload.
The JSON: {"version", "clip_sha256", "game_version" ("us" | "jp"), "rom_sha256", "usamune_fingerprint",
"global_timer", "area_update_counter", "level", "area", "act", "regions": [[physical begin, bytes], ...],
"boot": [{"begin", "base_sha256", "runs"}, ...], "pool": null | {"base", "base_sha256", "runs"}, "made_utc"}.
The payload: each region in order (the scene tap's task regions of the start frame, big-endian as the game
holds them), then "runs" main-pool runs of u32 offset, u32 length, bytes: the main pool's difference from the
reference practice reset named by "base".

A region named in "boot" (the game's .data, BOOT_ROWS: what the console copies from the ROM at boot) travels
as its difference from that boot image instead of whole: "runs" runs of u32 offset in the region, u32 length,
bytes, word-aligned. The receiver's own ROM supplies every other byte; "base_sha256" is the sha256 of that
ROM's boot image of the region, so a different ROM is refused with a plain sentence instead of rebuilding a
wrong start. Measured on his five Whomp's Fortress starts (2026-11-09): 99.5% of the 15,760-byte .data equals
the boot image (36-38 changed words). Version 2 (every region whole, no "boot") still reads.
Measured on his Whomp's Fortress takes (2026-11-08): regions alone 20.5-20.8 KB (Mario and every object exact
over 351-403 frames), with the pool delta 25.7-26.6 KB (byte-exact). A reader refuses version 1, another
version, a mismatched name and anything oversized.

The ROM: whoever holds it passes `rom=`; otherwise `_rom_candidates()` offers the ROMs this install knows (the
Ghost Lab's game files, then the lab config's), and only one whose bytes match is used. A downloaded start
whose rebuild needs a ROM this install does not have yet is still kept: the lab rebuilds it when it reads it.
"""
from __future__ import annotations

import hashlib
import json
import lzma
import os
import struct
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sm64_events.memory.layout import layout_for

MAGIC = b"SM64STRT"
VERSION = 3
READS = (2, 3)                       # versions a reader takes; this Trainer writes VERSION
SUFFIX = ".sm64start"
DOWNLOADED = "downloaded"
MAX_BLOB_BYTES = 64 << 10            # what the service carries per ghost; a real start is ~26 KB
MAX_PAYLOAD_BYTES = 512 << 10        # regions are 186,392 bytes and pool runs a few tens of KB
MAX_META_BYTES = 8 << 10
MAX_POOL_RUNS = 4096
_HEAD = struct.Struct("<8sII")
_RUN = struct.Struct("<II")
RDRAM_BYTES = 8 << 20
GAME_VERSIONS = ("us", "jp")
# The scene tap's task regions (layout.py rows): a start's regions are exactly these, in this order.
SPAN_ROWS = ("save_buffer", "scene_data", "power_meter_hud", "scene_bss", "slot_tail", "scene_engine_bss",
             "random_seed", "usamune_state")
# The regions that travel as their difference from the ROM's boot image: the game's .data, which IPL3 copies
# from cartridge 0x1000 to RAM 0x80246000 (1 MiB) at boot (tools/scene_lab/cold_boot.py does the same copy).
BOOT_ROWS = ("scene_data",)
BOOT_RAM, BOOT_ROM, BOOT_BYTES = 0x246000, 0x1000, 0x100000
MAX_BOOT_RUNS = 4096
DIFF_GAP = 8                         # unchanged bytes a run may carry rather than start a new run
ROM_DIFFERS = ("This ghost's start was made with a different Super Mario 64 Usamune ROM than yours, "
               "so it cannot be rebuilt here.")
ROM_MISSING = "This ghost's start needs your Super Mario 64 Usamune ROM to rebuild it, and this Trainer has none yet."


class StartError(ValueError):
    """This start cannot be used; the message says why in a plain sentence."""


@dataclass(frozen=True)
class BootDiff:
    """A region as it travels: its difference from the ROM's boot image. `runs` are (offset in the region,
    bytes); every other byte is the boot image's, which `base_sha256` names."""
    size: int
    base_sha256: str
    runs: tuple = ()


@dataclass
class StartFrame:
    clip_sha256: str
    game_version: str
    global_timer: int
    area_update_counter: int
    level: int
    area: int
    act: int
    regions: list = field(default_factory=list)        # [(physical begin, bytes | BootDiff until rebuilt)]
    rom_sha256: str = ""
    usamune_fingerprint: str = ""
    pool_base: str | None = None                       # the reference reset the pool delta was made against
    pool_base_sha256: str | None = None
    pool_runs: list = field(default_factory=list)      # [(physical begin, bytes)]
    made_utc: str = ""


def allowed_spans(game_version: str) -> list[tuple[int, int]]:
    """(physical begin, bytes) of every region a start may carry for that game version, in order."""
    layout = layout_for(game_version)
    spans = []
    for row in SPAN_ROWS:
        start, end = layout.value(f"{row}_start"), layout.value(f"{row}_end")
        if start is None or end is None:
            raise StartError(f"This Trainer does not know where a {game_version.upper()} start keeps its state.")
        spans.append((start & 0x1FFFFFFF, end - start))
    return spans


def main_pool(game_version: str) -> tuple[int, int]:
    layout = layout_for(game_version)
    return layout.main_pool_start & 0x1FFFFFFF, layout.main_pool_end & 0x1FFFFFFF


def _check_sha(text: str, what: str) -> None:
    if not isinstance(text, str) or len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        raise StartError(f"This start's {what} is not a sha256.")


def start_path(root: Path, clip_sha256: str, *, downloaded: bool = False) -> Path:
    _check_sha(clip_sha256, "ghost name")
    folder = Path(root) / "starts"
    return (folder / DOWNLOADED if downloaded else folder) / f"{clip_sha256}{SUFFIX}"


# ---------------------------------------------------------------- the boot image
def boot_spans(game_version: str) -> list[tuple[int, int]]:
    """(physical begin, bytes) of the regions that travel as their difference from the ROM's boot image."""
    spans = dict(zip(SPAN_ROWS, allowed_spans(game_version), strict=True))
    return [spans[row] for row in BOOT_ROWS]


def boot_image(rom: bytes, begin: int, size: int) -> bytes:
    """What the console holds at `begin` right after boot: IPL3's copy of the cartridge's first MiB of code."""
    if begin < BOOT_RAM or begin + size > BOOT_RAM + BOOT_BYTES:
        raise StartError("This start names a part of memory the ROM does not fill at boot.")
    at = BOOT_ROM + begin - BOOT_RAM
    if len(rom) < at + size:
        raise StartError("This ROM is too small to be a Super Mario 64 ROM.")
    return bytes(rom[at:at + size])


def boot_diff(data: bytes, base: bytes) -> BootDiff:
    """`data` as runs over `base`: word-aligned, a run carrying up to DIFF_GAP unchanged bytes before a new one."""
    size, spans = len(data), []
    for at in range(0, size, 4):
        if data[at:at + 4] == base[at:at + 4]:
            continue
        if spans and at - spans[-1][1] <= DIFF_GAP:
            spans[-1][1] = min(at + 4, size)
        else:
            spans.append([at, min(at + 4, size)])
    return BootDiff(size, hashlib.sha256(base).hexdigest(), tuple((low, bytes(data[low:high])) for low, high in spans))


def rebuild(diff: BootDiff, base: bytes) -> bytes:
    """The region whole again over `base`, which resolve() has matched to diff.base_sha256."""
    data = bytearray(base)
    for offset, run in diff.runs:
        data[offset:offset + len(run)] = run
    return bytes(data)


_ROM_CACHE: dict = {}


def _rom_candidates() -> list[Path]:
    """The ROM files this install knows: the Ghost Lab's game files (scenelab/roms.py, when this build has it),
    then the lab config's ROM (SM64_SCENE_LAB or the lab folder's lab.json). Never a network or a scan."""
    try:
        from sm64_events.scenelab import roms        # the Ghost Lab's ROM finder (I3)
    except ImportError:
        roms = None                                  # a build without the Ghost Lab's game files
    found = [] if roms is None else [path for path in map(roms.rom_for, GAME_VERSIONS) if path is not None]
    from sm64_events.core.paths import scene_lab_root
    configs = [Path(os.environ["SM64_SCENE_LAB"])] if os.environ.get("SM64_SCENE_LAB") else []
    configs.append(scene_lab_root() / "lab.json")
    for config in configs:
        try:
            rom = json.loads(config.read_text(encoding="utf-8")).get("rom")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(rom, str) and rom:
            found.append(Path(rom))
    return list(dict.fromkeys(found))


def _known_roms() -> list[tuple[str, bytes]]:
    """(sha256, the boot window) of every readable candidate ROM; each file hashed once per change."""
    known = []
    for path in _rom_candidates():
        try:
            stat = path.stat()
            key = (str(path), stat.st_size, stat.st_mtime_ns)
            if key not in _ROM_CACHE:
                if stat.st_size > 64 << 20:
                    continue
                data = path.read_bytes()
                _ROM_CACHE[key] = (hashlib.sha256(data).hexdigest(), data[:BOOT_ROM + BOOT_BYTES])
            known.append(_ROM_CACHE[key])
        except OSError:
            continue
    return known


def _rom_named(rom_sha256: str) -> bytes | None:
    """This install's ROM whose whole sha256 is `rom_sha256` (its first bytes through the boot window)."""
    return next((window for sha, window in _known_roms() if sha == rom_sha256), None)


def resolve(start: StartFrame, rom: bytes | None = None, *, required: bool = True) -> StartFrame:
    """The start with every boot region rebuilt from the ROM: `rom`, or this install's ROM whose boot image
    matches. StartError ROM_DIFFERS when a ROM is there but differs; ROM_MISSING when there is none (unless
    not `required`: the start comes back with those regions still as differences)."""
    pending = [(index, begin, data) for index, (begin, data) in enumerate(start.regions) if isinstance(data, BootDiff)]
    if not pending:
        return start
    regions = list(start.regions)
    windows = [bytes(rom)] if rom is not None else [window for _sha, window in _known_roms()]
    for index, begin, diff in pending:
        bases = []
        for window in windows:
            try:
                bases.append(boot_image(window, begin, diff.size))
            except StartError:
                continue                               # a file too small to be this game's ROM
        base = next((base for base in bases if hashlib.sha256(base).hexdigest() == diff.base_sha256), None)
        if base is None:
            if bases or required:
                raise StartError(ROM_DIFFERS if bases else ROM_MISSING)
            return start
        regions[index] = (begin, rebuild(diff, base))
    start.regions = regions
    return start


def is_resolved(start: StartFrame) -> bool:
    return not any(isinstance(data, BootDiff) for _begin, data in start.regions)


# ---------------------------------------------------------------- the format
def _size(data) -> int:
    return data.size if isinstance(data, BootDiff) else len(data)


def encode(start: StartFrame, rom: bytes | None = None) -> bytes:
    """Version 3. A boot region still whole travels as its difference from `rom`'s boot image (or this install's
    ROM named by `rom_sha256`); with neither it travels whole, which every reader still takes."""
    _check_regions(start.game_version, [(begin, _size(data)) for begin, data in start.regions])
    runs = [(int(begin), bytes(data)) for begin, data in start.pool_runs]
    _check_runs(start.game_version, [(begin, len(data)) for begin, data in runs])
    window = bytes(rom) if rom is not None else None
    boot_begins = {begin for begin, _size_ in boot_spans(start.game_version)}
    regions, boot = [], []
    for begin, data in start.regions:
        if begin in boot_begins and not isinstance(data, BootDiff):
            window = window if window is not None else _rom_named(start.rom_sha256)
            if window is not None:
                data = boot_diff(bytes(data), boot_image(window, begin, len(data)))
        if isinstance(data, BootDiff):
            if begin not in boot_begins:
                raise StartError("This start carries game memory outside the parts a start may hold.")
            _check_diff(data)
            boot.append({"begin": begin, "base_sha256": data.base_sha256, "runs": len(data.runs)})
        regions.append((begin, data))
    meta = {"version": VERSION, "clip_sha256": start.clip_sha256, "game_version": start.game_version,
            "rom_sha256": start.rom_sha256, "usamune_fingerprint": start.usamune_fingerprint,
            "global_timer": start.global_timer, "area_update_counter": start.area_update_counter,
            "level": start.level, "area": start.area, "act": start.act,
            "regions": [[begin, _size(data)] for begin, data in regions], "boot": boot,
            "pool": None if start.pool_base is None else {"base": start.pool_base, "base_sha256": start.pool_base_sha256,
                                                          "runs": len(runs)},
            "made_utc": start.made_utc or datetime.now(timezone.utc).isoformat()}
    payload = b"".join(
        b"".join(_RUN.pack(offset, len(run)) + run for offset, run in data.runs) if isinstance(data, BootDiff)
        else bytes(data) for _begin, data in regions) + b"".join(_RUN.pack(begin, len(data)) + data for begin, data in runs)
    text = json.dumps(meta, separators=(",", ":")).encode()
    blob = _HEAD.pack(MAGIC, VERSION, len(text)) + text + lzma.compress(payload, preset=9 | lzma.PRESET_EXTREME)
    if len(blob) > MAX_BLOB_BYTES:
        raise StartError("This start is larger than a ghost may carry.")
    return blob


def _check_regions(game_version: str, regions: list[tuple[int, int]]) -> None:
    if game_version not in GAME_VERSIONS:
        raise StartError("This start names a game version this Trainer does not know.")
    if [(int(begin), int(size)) for begin, size in regions] != allowed_spans(game_version):
        raise StartError("This start carries game memory outside the parts a start may hold.")


def _check_diff(diff: BootDiff) -> None:
    _check_sha(diff.base_sha256, "ROM reference")
    if len(diff.runs) > MAX_BOOT_RUNS:
        raise StartError("This start's game data changes are more than any real start makes.")
    end = 0
    for offset, run in diff.runs:
        if not run or offset < end or offset + len(run) > diff.size:
            raise StartError("This start changes game data outside its part, or out of order.")
        end = offset + len(run)


def _check_runs(game_version: str, runs: list[tuple[int, int]]) -> None:
    low, high = main_pool(game_version)
    if len(runs) > MAX_POOL_RUNS:
        raise StartError("This start's main-pool changes are more than any real start makes.")
    end = low
    for begin, size in runs:
        if size <= 0 or begin < end or begin + size > high:
            raise StartError("This start changes memory outside the main pool, or out of order.")
        end = begin + size


def _unpack(data: bytes) -> tuple[int, dict, bytes]:
    """(version, header, payload): every length bounded before it is read, the payload bounded while it unpacks."""
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_BLOB_BYTES or len(data) < _HEAD.size:
        raise StartError("This start is the wrong size.")
    magic, version, length = _HEAD.unpack_from(data)
    if magic != MAGIC:
        raise StartError("This is not a ghost start.")
    if version not in READS:
        raise StartError(f"This start is version {version}; this Trainer reads versions "
                         f"{' and '.join(str(known) for known in READS)}.")
    if length > MAX_META_BYTES or _HEAD.size + length > len(data):
        raise StartError("This start's header is the wrong size.")
    try:
        meta = json.loads(bytes(data[_HEAD.size:_HEAD.size + length]))
        decompressor = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
        payload = decompressor.decompress(bytes(data[_HEAD.size + length:]), max_length=MAX_PAYLOAD_BYTES + 1)
    except (ValueError, lzma.LZMAError, UnicodeDecodeError) as error:
        raise StartError(f"This start is damaged: {error}") from None
    if len(payload) > MAX_PAYLOAD_BYTES or not decompressor.eof or decompressor.unused_data:
        raise StartError("This start unpacks to more than any real start holds.")
    if not isinstance(meta, dict) or meta.get("version") != version:
        raise StartError("This start's header is incomplete.")
    return version, meta, payload


def _runs_at(payload: bytes, at: int, count: int, what: str) -> tuple[list, int]:
    runs = []
    for _ in range(count):
        if at + _RUN.size > len(payload):
            raise StartError(f"This start's {what} are cut short.")
        begin, size = _RUN.unpack_from(payload, at)
        at += _RUN.size
        if at + size > len(payload):
            raise StartError(f"This start's {what} are cut short.")
        runs.append((begin, bytes(payload[at:at + size])))
        at += size
    return runs, at


def _split(payload: bytes, regions_meta: list, boot: dict, count: int) -> tuple[list, list]:
    """The regions (a boot one as its BootDiff), then `count` pool runs; every byte of the payload named."""
    at, regions = 0, []
    for begin, size in regions_meta:
        if begin in boot:
            base_sha256, runs_count = boot[begin]
            runs, at = _runs_at(payload, at, runs_count, "game data changes")
            diff = BootDiff(size, base_sha256, tuple(runs))
            _check_diff(diff)
            regions.append((begin, diff))
            continue
        if at + size > len(payload):
            raise StartError("This start's regions are cut short.")
        regions.append((begin, bytes(payload[at:at + size])))
        at += size
    if count > MAX_POOL_RUNS:
        raise StartError("This start's main-pool changes are more than any real start makes.")
    runs, at = _runs_at(payload, at, count, "main-pool changes")
    if at != len(payload):
        raise StartError("This start has bytes its header does not name.")
    return regions, runs


def _boot_meta(version: int, meta: dict, game_version: str) -> dict:
    """{begin: (base_sha256, runs)} of the regions that travel as differences; only BOOT_ROWS may."""
    if version == 2:
        if "boot" in meta:
            raise StartError("This start's header is incomplete.")
        return {}
    entries = meta.get("boot")
    if not isinstance(entries, list):
        raise StartError("This start's header is incomplete.")
    allowed, boot = {begin for begin, _size_ in boot_spans(game_version)}, {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"begin", "base_sha256", "runs"} \
                or type(entry["begin"]) is not int or type(entry["runs"]) is not int:
            raise StartError("This start's header is incomplete.")
        if entry["begin"] not in allowed or entry["begin"] in boot:
            raise StartError("This start carries game memory outside the parts a start may hold.")
        if not 0 <= entry["runs"] <= MAX_BOOT_RUNS:
            raise StartError("This start's game data changes are more than any real start makes.")
        _check_sha(entry["base_sha256"], "ROM reference")
        boot[entry["begin"]] = (entry["base_sha256"], entry["runs"])
    return boot


def decode(data: bytes, clip_sha256: str | None = None) -> StartFrame:
    """The start in a blob, bounded and checked; StartError with a plain sentence otherwise. A boot region
    comes back as its BootDiff: resolve() rebuilds it from the ROM."""
    version, meta, payload = _unpack(data)
    try:
        game_version = meta["game_version"]
        regions_meta = [(int(begin), int(size)) for begin, size in meta["regions"]]
        pool = meta.get("pool")
        numbers = [int(meta[name]) for name in ("global_timer", "area_update_counter", "level", "area", "act")]
        for name in ("clip_sha256", "rom_sha256", "usamune_fingerprint"):
            _check_sha(meta[name], name.replace("_", " "))
        count = int(pool["runs"]) if isinstance(pool, dict) else 0
    except (KeyError, TypeError, ValueError) as error:
        raise StartError(f"This start's header is incomplete: {error}") from None
    if clip_sha256 is not None and meta["clip_sha256"] != clip_sha256:
        raise StartError("This start belongs to another ghost.")
    _check_regions(game_version, regions_meta)
    regions, runs = _split(payload, regions_meta, _boot_meta(version, meta, game_version), count)
    _check_runs(game_version, [(begin, len(data)) for begin, data in runs])
    if pool is not None:
        _check_sha(pool.get("base_sha256"), "reference")
        if not isinstance(pool.get("base"), str) or len(pool["base"]) > 200:
            raise StartError("This start names its reference in a way this Trainer does not read.")
    return StartFrame(meta["clip_sha256"], game_version, *numbers, regions, meta["rom_sha256"],
                      meta["usamune_fingerprint"], pool["base"] if pool else None, pool["base_sha256"] if pool else None,
                      runs, str(meta.get("made_utc", ""))[:64])


# ---------------------------------------------------------------- the binding to its ghost
UNKNOWN = object()      # a word of a boot region its runs do not carry: the ROM's, not known without it


def _word(start: StartFrame, address: int, size: int):
    """The start's bytes at `address`; None outside its regions; UNKNOWN where only the ROM knows them."""
    at = address & 0x1FFFFFFF
    for begin, data in start.regions:
        if begin <= at and at + size <= begin + _size(data):
            if not isinstance(data, BootDiff):
                return data[at - begin:at - begin + size]
            offset = at - begin
            return next((run[offset - low:offset - low + size] for low, run in data.runs
                         if low <= offset and offset + size <= low + len(run)), UNKNOWN)
    return None


def _same(word, value: int) -> bool:
    return word is UNKNOWN or (word is not None and int.from_bytes(word, "big") == value)


def bind(start: StartFrame, first_record) -> None:
    """The start is this ghost's first record: its header and its own regions say the same clocks, level and
    area as the record, and Mario's action and position equal the record's pose. StartError otherwise.
    A word only the ROM knows (a boot region not yet rebuilt) is checked once resolve() has rebuilt it: the
    lab binds again after it reads the start with its ROM."""
    layout = layout_for(start.game_version)
    header = (start.global_timer, start.area_update_counter, start.level, start.area)
    record = (first_record.global_timer, first_record.area_update_counter, first_record.level, first_record.area)
    if header != record:
        raise StartError("This start does not begin where its ghost begins.")
    words = [_word(start, address, size) for address, size in (
        (layout.global_timer, 4), (layout.area_update_counter, 2), (layout.curr_level, 2), (layout.curr_area, 2))]
    if not all(_same(word, value) for word, value in zip(words, header, strict=True)):
        raise StartError("This start's game state disagrees with its own header.")
    pose = _pose(first_record)
    if pose is not None:
        action = _word(start, layout.mario_struct + 0x0C, 4)
        position = _word(start, layout.mario_struct + 0x3C, 12)
        if not _same(action, pose[1]) or position is None \
                or (position is not UNKNOWN and struct.unpack(">3f", position) != pose[0]):
            raise StartError("This start's Mario is not where its ghost's Mario begins.")


def _pose(record) -> tuple[tuple, int] | None:
    from sm64_events.ghost.native_layout import POSE_AT, VALID_POSE
    if not record.valid_fields & VALID_POSE:
        return None
    return struct.unpack_from("<3f", record.raw, POSE_AT), struct.unpack_from("<I", record.raw, POSE_AT + 40)[0]


# ---------------------------------------------------------------- the files
def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def write_start(root: Path, start: StartFrame, rom: bytes | None = None) -> Path:
    """His own start (made on this machine from his own attempt scene): atomically, its .data kept as the
    difference from `rom`'s boot image (or this install's ROM named by its rom_sha256), as it travels."""
    return _write(start_path(root, start.clip_sha256), encode(start, rom))


def _read_blob(root: Path, clip_sha256: str, downloaded: bool) -> bytes | None:
    path = start_path(root, clip_sha256, downloaded=downloaded)
    if not path.is_file():
        return None
    with path.open("rb") as stream:
        return stream.read(MAX_BLOB_BYTES + 1)


def read_start(root: Path, clip_sha256: str, *, downloaded: bool = False, rom: bytes | None = None) -> StartFrame | None:
    """The clip's start, every region whole (rebuilt from `rom`, or this install's matching ROM), None when it
    has none; StartError when the file cannot be used or no matching ROM is here (ROM_DIFFERS, ROM_MISSING).
    A downloaded one is still untrusted after this: the lab checks it against the ROM before it simulates."""
    data = _read_blob(root, clip_sha256, downloaded)
    return None if data is None else resolve(decode(data, clip_sha256), rom)


def start_blob(root: Path, clip_sha256: str) -> bytes | None:
    """FROZEN (community/ghost_start.py's start_for_upload): his own start of this ghost as it travels (versioned
    header: game version, ROM sha256, Usamune build fingerprint), or None when this ghost has none or its file
    cannot be used. Never a downloaded start: only his own travel. It travels without its main-pool changes: a
    receiver never applies those from another machine (the main pool holds ROM-made scripts), so they would only
    cost bytes; its regions alone keep Mario and every object exact. Its .data travels as its difference from
    the boot image (an older whole file is diffed here when this install has its ROM), so the receiver's own
    ROM supplies the rest. No ROM is needed to send a start already kept that way."""
    try:
        data = _read_blob(root, clip_sha256, False)
        if data is None:
            return None
        start = decode(data, clip_sha256)
        start.pool_base = start.pool_base_sha256 = None
        start.pool_runs = []
        return encode(start)
    except StartError:
        return None


def accept_start_blob(root: Path, clip_bytes: bytes, blob: bytes) -> Path:
    """FROZEN (community/ghost_start.py's accept_download): a start that came with a downloaded ghost. It is
    checked here (bounds, version, spans, the binding to this ghost's first record) and kept apart from his
    own starts as untrusted; StartError with a plain sentence otherwise, and nothing is written. Its .data is
    rebuilt from his own ROM when this install has one: a ROM that differs refuses it now (ROM_DIFFERS); with
    no ROM yet it is kept as it came and the lab rebuilds it when it reads it."""
    from sm64_events.ghost.clip import decode_clip
    clip_sha256 = hashlib.sha256(clip_bytes).hexdigest()
    start = resolve(decode(blob, clip_sha256), required=False)
    clip = decode_clip(clip_bytes)
    if not clip.frames:
        raise StartError("This ghost has no records to bind its start to.")
    bind(start, clip.frames[0])
    return _write(start_path(root, clip_sha256, downloaded=True), bytes(blob))


def remove_start(root: Path, clip_sha256: str) -> None:
    """With its clip: whoever deletes a ghost recording deletes its starts too."""
    for downloaded in (False, True):
        start_path(root, clip_sha256, downloaded=downloaded).unlink(missing_ok=True)


def holdings(root: Path) -> list[dict]:
    """GHOST FILES' owner listing (ghost/storage.py): every start file here, {"sha256": its clip's, "bytes",
    "path", "downloaded"}. A start goes when the last clip of its sha goes; storage decides that."""
    found = []
    for downloaded in (False, True):
        folder = Path(root) / "starts" / (DOWNLOADED if downloaded else "")
        if not folder.is_dir():
            continue
        for path in folder.glob(f"*{SUFFIX}"):
            name = path.name[:-len(SUFFIX)]
            try:
                _check_sha(name, "ghost name")
                found.append({"sha256": name, "bytes": path.stat().st_size, "path": str(path), "downloaded": downloaded})
            except (StartError, OSError):
                continue
    return found


def remove_holdings(root: Path, shas) -> dict:
    """Deletes the starts of these clips (both kinds); {"deleted": [sha], "bytes", "skipped": [sha]}. A file
    Windows will not let go of is skipped, never retried in a loop."""
    deleted, skipped, freed = [], [], 0
    for sha in dict.fromkeys(shas):
        gone = True
        for downloaded in (False, True):
            path = start_path(root, sha, downloaded=downloaded)
            try:
                size = path.stat().st_size
                path.unlink()
                freed += size
            except FileNotFoundError:
                continue
            except OSError:
                gone = False
        (deleted if gone else skipped).append(sha)
    return {"deleted": deleted, "bytes": freed, "skipped": skipped}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
