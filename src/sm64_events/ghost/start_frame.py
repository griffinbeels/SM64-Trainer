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

Format v2 (little-endian): magic SM64STRT, u32 version, u32 JSON length, the JSON, then xz of the payload.
The JSON: {"version", "clip_sha256", "game_version" ("us" | "jp"), "rom_sha256", "usamune_fingerprint",
"global_timer", "area_update_counter", "level", "area", "act", "regions": [[physical begin, bytes], ...],
"pool": null | {"base", "base_sha256", "runs"}, "made_utc"}. The payload: the regions' bytes in order (the
scene tap's task regions of the start frame, big-endian as the game holds them), then "runs" main-pool runs
of u32 offset, u32 length, bytes: the main pool's difference from the reference practice reset named by
"base". Measured on his Whomp's Fortress takes (2026-11-08): regions alone 20.5-20.8 KB (Mario and every
object exact over 351-403 frames), with the pool delta 25.7-26.6 KB (byte-exact). A reader refuses
version 1, another version, a mismatched name and anything oversized.
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
VERSION = 2
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


class StartError(ValueError):
    """This start cannot be used; the message says why in a plain sentence."""


@dataclass
class StartFrame:
    clip_sha256: str
    game_version: str
    global_timer: int
    area_update_counter: int
    level: int
    area: int
    act: int
    regions: list = field(default_factory=list)        # [(physical begin, bytes)]
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


# ---------------------------------------------------------------- the format
def encode(start: StartFrame) -> bytes:
    _check_regions(start.game_version, [(begin, len(data)) for begin, data in start.regions])
    runs = [(int(begin), bytes(data)) for begin, data in start.pool_runs]
    _check_runs(start.game_version, [(begin, len(data)) for begin, data in runs])
    meta = {"version": VERSION, "clip_sha256": start.clip_sha256, "game_version": start.game_version,
            "rom_sha256": start.rom_sha256, "usamune_fingerprint": start.usamune_fingerprint,
            "global_timer": start.global_timer, "area_update_counter": start.area_update_counter,
            "level": start.level, "area": start.area, "act": start.act,
            "regions": [[begin, len(data)] for begin, data in start.regions],
            "pool": None if start.pool_base is None else {"base": start.pool_base, "base_sha256": start.pool_base_sha256,
                                                          "runs": len(runs)},
            "made_utc": start.made_utc or datetime.now(timezone.utc).isoformat()}
    payload = b"".join(bytes(data) for _begin, data in start.regions) + b"".join(
        _RUN.pack(begin, len(data)) + data for begin, data in runs)
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


def _check_runs(game_version: str, runs: list[tuple[int, int]]) -> None:
    low, high = main_pool(game_version)
    if len(runs) > MAX_POOL_RUNS:
        raise StartError("This start's main-pool changes are more than any real start makes.")
    end = low
    for begin, size in runs:
        if size <= 0 or begin < end or begin + size > high:
            raise StartError("This start changes memory outside the main pool, or out of order.")
        end = begin + size


def _unpack(data: bytes) -> tuple[dict, bytes]:
    """(header, payload): every length bounded before it is read, the payload bounded while it unpacks."""
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_BLOB_BYTES or len(data) < _HEAD.size:
        raise StartError("This start is the wrong size.")
    magic, version, length = _HEAD.unpack_from(data)
    if magic != MAGIC:
        raise StartError("This is not a ghost start.")
    if version != VERSION:
        raise StartError(f"This start is version {version}; this Trainer reads version {VERSION}.")
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
    if not isinstance(meta, dict):
        raise StartError("This start's header is incomplete.")
    return meta, payload


def _split(payload: bytes, regions_meta: list, count: int) -> tuple[list, list]:
    """The regions' bytes, then `count` pool runs; every byte of the payload named."""
    at, regions = 0, []
    for begin, size in regions_meta:
        if at + size > len(payload):
            raise StartError("This start's regions are cut short.")
        regions.append((begin, bytes(payload[at:at + size])))
        at += size
    if count > MAX_POOL_RUNS:
        raise StartError("This start's main-pool changes are more than any real start makes.")
    runs = []
    for _ in range(count):
        if at + _RUN.size > len(payload):
            raise StartError("This start's main-pool changes are cut short.")
        begin, size = _RUN.unpack_from(payload, at)
        at += _RUN.size
        if at + size > len(payload):
            raise StartError("This start's main-pool changes are cut short.")
        runs.append((begin, bytes(payload[at:at + size])))
        at += size
    if at != len(payload):
        raise StartError("This start has bytes its header does not name.")
    return regions, runs


def decode(data: bytes, clip_sha256: str | None = None) -> StartFrame:
    """The start in a blob, bounded and checked; StartError with a plain sentence otherwise."""
    meta, payload = _unpack(data)
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
    regions, runs = _split(payload, regions_meta, count)
    _check_runs(game_version, [(begin, len(data)) for begin, data in runs])
    if pool is not None:
        _check_sha(pool.get("base_sha256"), "reference")
        if not isinstance(pool.get("base"), str) or len(pool["base"]) > 200:
            raise StartError("This start names its reference in a way this Trainer does not read.")
    return StartFrame(meta["clip_sha256"], game_version, *numbers, regions, meta["rom_sha256"],
                      meta["usamune_fingerprint"], pool["base"] if pool else None, pool["base_sha256"] if pool else None,
                      runs, str(meta.get("made_utc", ""))[:64])


# ---------------------------------------------------------------- the binding to its ghost
def _word(start: StartFrame, address: int, size: int) -> bytes | None:
    at = address & 0x1FFFFFFF
    for begin, data in start.regions:
        if begin <= at and at + size <= begin + len(data):
            return data[at - begin:at - begin + size]
    return None


def bind(start: StartFrame, first_record) -> None:
    """The start is this ghost's first record: its header and its own regions say the same clocks, level and
    area as the record, and Mario's action and position equal the record's pose. StartError otherwise."""
    layout = layout_for(start.game_version)
    header = (start.global_timer, start.area_update_counter, start.level, start.area)
    record = (first_record.global_timer, first_record.area_update_counter, first_record.level, first_record.area)
    if header != record:
        raise StartError("This start does not begin where its ghost begins.")
    words = [_word(start, address, size) for address, size in (
        (layout.global_timer, 4), (layout.area_update_counter, 2), (layout.curr_level, 2), (layout.curr_area, 2))]
    if None in words or tuple(int.from_bytes(word, "big") for word in words) != header:
        raise StartError("This start's game state disagrees with its own header.")
    pose = _pose(first_record)
    if pose is not None:
        action = _word(start, layout.mario_struct + 0x0C, 4)
        position = _word(start, layout.mario_struct + 0x3C, 12)
        if action is None or position is None or int.from_bytes(action, "big") != pose[1] \
                or struct.unpack(">3f", position) != pose[0]:
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


def write_start(root: Path, start: StartFrame) -> Path:
    """His own start (made on this machine from his own attempt scene): atomically."""
    return _write(start_path(root, start.clip_sha256), encode(start))


def read_start(root: Path, clip_sha256: str, *, downloaded: bool = False) -> StartFrame | None:
    """The clip's start, None when it has none; StartError when the file cannot be used. A downloaded one is
    still untrusted after this: the lab checks it against the ROM before it simulates anything."""
    path = start_path(root, clip_sha256, downloaded=downloaded)
    if not path.is_file():
        return None
    with path.open("rb") as stream:
        data = stream.read(MAX_BLOB_BYTES + 1)
    return decode(data, clip_sha256)


def start_blob(root: Path, clip_sha256: str) -> bytes | None:
    """FROZEN (community/ghost_start.py's start_for_upload): his own start of this ghost as it travels (versioned
    header: game version, ROM sha256, Usamune build fingerprint), or None when this ghost has none or its file
    cannot be used. Never a downloaded start: only his own travel. It travels without its main-pool changes: a
    receiver never applies those from another machine (the main pool holds ROM-made scripts), so they would only
    cost bytes; its regions alone keep Mario and every object exact (about 20.5 KB)."""
    try:
        start = read_start(root, clip_sha256)
    except StartError:
        return None
    if start is None:
        return None
    start.pool_base = start.pool_base_sha256 = None
    start.pool_runs = []
    return encode(start)


def accept_start_blob(root: Path, clip_bytes: bytes, blob: bytes) -> Path:
    """FROZEN (community/ghost_start.py's accept_download): a start that came with a downloaded ghost. It is
    checked here (bounds, version, spans, the binding to this ghost's first record) and kept apart from his
    own starts as untrusted; StartError with a plain sentence otherwise, and nothing is written."""
    from sm64_events.ghost.clip import decode_clip
    clip_sha256 = hashlib.sha256(clip_bytes).hexdigest()
    start = decode(blob, clip_sha256)
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
