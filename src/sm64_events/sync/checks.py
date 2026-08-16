"""The verdict functions gates call — pure, testable without an emulator.

Each answers one question about numbers a gate already read: does this
counter tick like a frame counter, where in an RDRAM image does a displayed
time live, on which sample did Mario's action enter a set. The gate modules
(`address_gates.py`, `calibration_gates.py`, `feature_gates.py`) do the
reading and prompting; nothing here touches memory or the human.

`parse_frames` and the exact-value scan are lifted from `tools/hunt_value.py`
(2026-06-10, the tool that found Usamune's globals on US) so a JP hunt runs
the same arithmetic the US one did.
"""
import array
import re
from collections.abc import Callable, Iterable

from sm64_events.memory.addresses import KSEG0_BASE
from sm64_events.sync.gates import Verdict

FRAME_TOLERANCE = 2   # display-tick / freeze-ordering slack, in frames


# --- counters ---------------------------------------------------------------

def ticks_per_second(samples: list[tuple[float, int]]) -> float:
    """Average u32 growth per wall second over (wall_seconds, value) samples."""
    if len(samples) < 2:
        return 0.0
    (t0, v0), (t1, v1) = samples[0], samples[-1]
    elapsed = t1 - t0
    if elapsed <= 0:
        return 0.0
    return ((v1 - v0) & 0xFFFFFFFF) / elapsed


def check_ticks(samples: list[tuple[float, int]], lo: float = 25.0,
                hi: float = 35.0, what: str = "global_timer") -> Verdict:
    """A 30 fps frame counter, read over wall time — the contract every
    version's global timer must satisfy (verify_addresses.py's Phase 1)."""
    rate = ticks_per_second(samples)
    measured = {"per_second": round(rate, 2), "samples": len(samples)}
    if lo <= rate <= hi:
        return Verdict("verified", measured=measured,
                       evidence=f"{what} ticks {rate:.1f}/s over "
                                f"{samples[-1][0] - samples[0][0]:.1f}s")
    return Verdict("failed", measured=measured,
                   evidence=f"{what} ticks {rate:.1f}/s; expected {lo}-{hi}")


def check_equals(read: int, expected: int, what: str) -> Verdict:
    if read == expected:
        return Verdict("verified", evidence=f"{what} reads {read}")
    return Verdict("failed", measured={"read": read, "expected": expected},
                   evidence=f"{what} reads {read}, expected {expected}")


# --- the exact-value hunt ---------------------------------------------------

def parse_frames(text: str) -> int | None:
    """A displayed Usamune time as 30 fps frames: `0'20"20` -> 606; `20.2`
    (seconds) -> 606; `f606` -> 606. None for blank input."""
    text = text.strip()
    if not text:
        return None
    if text.startswith("f"):
        return int(text[1:])
    match = re.fullmatch(r"(\d+)\s*[':]\s*(\d+)\s*[\".:]\s*(\d+)", text)
    if match:
        mins, secs, cents = (int(group) for group in match.groups())
        return mins * 1800 + secs * 30 + round(cents * 30 / 100)
    return round(float(text) * 30)


def scan_u16(image: bytes, value: int, tolerance: int = FRAME_TOLERANCE) -> list[int]:
    """N64 addresses of every aligned u16 within `tolerance` of `value` in a
    raw RDRAM image as PJ64 stores it (little-endian 32-bit words, so the
    halfword at N64 offset o sits at host offset o ^ 2 — memory/base.py)."""
    lo, hi = value - tolerance, value + tolerance
    halves = array.array("H", image)
    return [KSEG0_BASE + ((index * 2) ^ 2)
            for index, found in enumerate(halves) if lo <= found <= hi]


def scan_u32(image: bytes, value: int, tolerance: int = 0) -> list[int]:
    lo, hi = value - tolerance, value + tolerance
    words = array.array("I", image)
    return [KSEG0_BASE + index * 4
            for index, found in enumerate(words) if lo <= found <= hi]


def scan_ticking_u16(image_before: bytes, image_after: bytes, seconds: float,
                     lo: float = 25.0, hi: float = 35.0) -> list[int]:
    """N64 addresses of every aligned u16 that ADVANCED like a 30 fps counter
    between two images taken `seconds` apart (u16 wrap allowed). This is how
    a RUNNING Usamune timer is hunted: the value he types has moved on by the
    time he finishes typing, so an exact-value scan of a fresh image never
    holds it (review finding, 2026-08-15) — but the address that ticks at
    game rate is a handful, and the typed value then only has to be NEAR."""
    before = array.array("H", image_before)
    after = array.array("H", image_after)
    lo_delta, hi_delta = lo * seconds, hi * seconds
    hits = []
    for index, (was, now) in enumerate(zip(before, after)):
        delta = (now - was) & 0xFFFF
        if lo_delta <= delta <= hi_delta:
            hits.append(KSEG0_BASE + ((index * 2) ^ 2))
    return hits


def near(image: bytes, address: int, value: int, tolerance: int) -> bool:
    """Whether the aligned u16 at `address` in `image` is within `tolerance`
    of `value` (host order as PJ64 stores it)."""
    host = (address - KSEG0_BASE) ^ 2
    found = int.from_bytes(image[host:host + 2], "little")
    return abs(found - value) <= tolerance


def survivors(candidate_sets: Iterable[Iterable[int]]) -> list[int]:
    """Addresses present in EVERY scan — the hunt's intersection step."""
    result: set[int] | None = None
    for candidates in candidate_sets:
        result = set(candidates) if result is None else result & set(candidates)
    return sorted(result or ())


# --- action edges over a snapshot stream ------------------------------------

def first_edge(snaps: list, into: frozenset[int], start: int = 1) -> int | None:
    """Index of the first sample whose mario_action is in `into` while the
    previous sample's was not — the entry edge every detector keys on."""
    for index in range(max(start, 1), len(snaps)):
        if (snaps[index].mario_action in into
                and snaps[index - 1].mario_action not in into):
            return index
    return None


def frames_between(snaps: list, earlier: int, later: int) -> int:
    return snaps[later].global_timer - snaps[earlier].global_timer


def await_event(events: Iterable[dict], type_: str,
                predicate: Callable[[dict], bool] = lambda payload: True) -> dict | None:
    """The first wire event of `type_` whose payload satisfies `predicate`,
    or None. `events` are `core/events.py::to_wire` dicts (or anything with
    "type" and "payload")."""
    for event in events:
        if event.get("type") == type_ and predicate(event.get("payload", {})):
            return event
    return None


def pool_contains(pool_base: int, pointer: int, slot_size: int = 0x260,
                  slots: int = 240) -> bool:
    """True when `pointer` lands on a slot BOUNDARY inside the pool — the
    same test that discovered which gMarioState words hold objects."""
    if not (pool_base <= pointer < pool_base + slots * slot_size):
        return False
    return (pointer - pool_base) % slot_size == 0


# --- textbox open-state (lifted from tools/probe_textbox.py::box_opens) ----

def box_open_index(snaps: list, reading_index: int,
                   thresholds: dict[int, int | None]) -> int | None:
    """Index of the first sample, at or after `reading_index`, whose
    `mario_action_state` reaches the box-open threshold for the action AT
    `reading_index` — the frame the game itself creates the dialog box,
    per `addresses.BOX_OPENS_AT_STATE`. None when that action carries no
    known threshold (an explicit None entry, or no entry at all) or the
    window ends before the state gets there. `probe_textbox.py::box_opens`
    runs the identical rule over its own sample dicts; this is the same
    algorithm over `GameSnapshot`s so a calibration gate can drive it from
    `ctx.snapshots()`."""
    action = snaps[reading_index].mario_action
    threshold = thresholds.get(action)
    if threshold is None:
        return None
    for index in range(reading_index, len(snaps)):
        if (snaps[index].mario_action == action
                and snaps[index].mario_action_state >= threshold):
            return index
    return None


# --- resolving a calibration gate's `backs` -- WHAT it measures against ----

def resolve_backs(dotted: str):
    """The live value of the constant a calibration gate's `backs` names —
    `"pkg.module.Class.ATTR"` or `"pkg.module.ATTR"`. Imports the LONGEST
    importable prefix, then walks `getattr` for the rest, so a class
    attribute and a bare module constant resolve the same way. Raises
    (ImportError/AttributeError) on a name that does not resolve — that
    failure IS `tests/test_gates_cover.py`'s enforcement that every `backs`
    points at something real, and it is why no calibration check may ever
    restate the number this returns as a literal."""
    import importlib

    parts = dotted.split(".")
    module = None
    split_at = len(parts)
    while split_at > 0:
        try:
            module = importlib.import_module(".".join(parts[:split_at]))
            break
        except ImportError:
            split_at -= 1
    if module is None:
        raise ImportError(f"no importable module prefix in {dotted!r}")
    value = module
    for attr in parts[split_at:]:
        value = getattr(value, attr)
    return value
