"""Pure selection of certified rivals against the selected practice rank.

The population provider owns admission, clip validation and storage. This
module consumes bounded metadata snapshots; it never opens clips or fetches
them. Manual choices occupy stable slots independently of automatic ranges.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from itertools import islice

from sm64_events.ghost.native_session import ghost_hue
from sm64_events.ranks import classify, curves, scoring
from sm64_events.ranks.calibration import resolve_curve
from sm64_events.ranks.standards import entity_key

MAX_RIVALS = 4
MAX_CANDIDATES = 16384
MAX_AHEAD = 10
MAX_STEP = scoring.progression_key(classify.RANK_NAMES[0], scoring.DIVISION_NUMERALS[-1])
PB_HUE_DEGREES = 250
DEFAULT_OPTIONS = {
    "mode": "adaptive", "count": MAX_RIVALS, "include_pb": True,
    "min_ahead": 1, "max_ahead": 5,
    "min_step": 0, "max_step": MAX_STEP,
}
_IDENTITY_VERSION = object()
MATCH_SCOPES = ("strategy", "overall")


def _checked_scope(scope):
    if scope not in MATCH_SCOPES:
        raise ValueError("Choose Strategy or Overall ghosts.")
    return scope


@dataclass(frozen=True)
class PracticeIdentity:
    kind: str
    entity_key: str
    clock: str
    game_version: str
    strategy: str | None
    signature: str | None = None
    scope: str = "strategy"

    def __post_init__(self):
        _checked_scope(self.scope)


def rank_entity_key(identity: PracticeIdentity) -> str:
    """Adapt the ghost key through the rank store's canonical key builder."""
    if identity.kind == "segment":
        return entity_key(None, None, int(identity.entity_key))
    if identity.kind == "star":
        course, star = map(int, identity.entity_key.split("-"))
        return entity_key(course, star)
    raise ValueError("A rival belongs to a star or segment.")


def _read_context(ranks):
    return ranks.read_context() if hasattr(ranks, "read_context") else nullcontext()


def _rank_model(ranks, key, identity, version):
    return (resolve_curve(ranks, key, version) if identity.scope == "overall"
            else ranks.ladder_cs(key, identity.strategy, version))


def _progress(model, frames, scope):
    if not model:
        return None
    result = (curves.progress_for_time(model, classify.display_cs(frames)) if scope == "overall"
              else scoring.progress_for_time(model, classify.display_cs(frames)))
    if result is None:
        return None
    return {"tier": result["tier"], "division": result["division"],
            "step": scoring.progression_key(result["tier"], result["division"])}


def rank_frames(ranks, identity: PracticeIdentity, frames: int, *, version=_IDENTITY_VERSION) -> dict | None:
    """Grade the selected scope; saved times may name their original ROM.

    Omitting version grades a recording on the observed ROM. Explicit None is
    the existing legacy saved-time rule: follow the store's grading version.
    """
    version = identity.game_version if version is _IDENTITY_VERSION else version
    if (ranks is None or type(frames) is not int or frames <= 0
            or identity.scope == "strategy" and not identity.strategy
            or identity.game_version not in ("us", "jp") or version not in (None, "us", "jp")):
        return None
    with _read_context(ranks):
        key = rank_entity_key(identity)
        if ranks.clock_for(key) != identity.clock:
            return None
        return _progress(_rank_model(ranks, key, identity, version), frames, identity.scope)


def checked_options(value: dict) -> dict:
    """Validate the complete settings draft without changing its caller."""
    if not isinstance(value, dict) or set(value) - (DEFAULT_OPTIONS.keys() | {"strategy_only"}):
        raise ValueError("Choose valid rival settings.")
    if "strategy_only" in value and type(value["strategy_only"]) is not bool:
        raise ValueError("Choose valid rival settings.")
    # Old persisted drafts keep their other choices; every strategy now has its
    # own rivals, so the former opt-in field no longer controls selection.
    result = DEFAULT_OPTIONS | {key: item for key, item in value.items() if key != "strategy_only"}
    if result["mode"] not in ("adaptive", "fixed"):
        raise ValueError("Choose how the rival range follows your practice.")
    if type(result["include_pb"]) is not bool:
        raise ValueError("Choose valid rival settings.")
    limits = {"count": (1, MAX_RIVALS), "min_ahead": (0, MAX_AHEAD), "max_ahead": (0, MAX_AHEAD),
              "min_step": (0, MAX_STEP), "max_step": (0, MAX_STEP)}
    for key, (low, high) in limits.items():
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError("Choose a rival range within the available ranks.")
    if result["min_ahead"] > result["max_ahead"] or result["min_step"] > result["max_step"]:
        raise ValueError("Minimum rank cannot exceed maximum rank.")
    return result


def _bounded(rows):
    result = tuple(islice(rows, MAX_CANDIDATES + 1))
    if len(result) > MAX_CANDIDATES:
        raise ValueError("This rival metadata snapshot exceeds its limit.")
    return result


def _bundled(row):
    """Only the pinned beta provider assigns this separate historical source."""
    return (row.get("source_status") == "bundled-author-recording"
            and row.get("structurally_valid") is True and row.get("certified") is False
            and row.get("full_start") is None
            and row.get("recorded_game_version") in (None, "us", "jp"))


def _admitted(row):
    """Community structural validation carries no honest-gameplay certificate."""
    return (_bundled(row) or row.get('certified') is True or row.get('provenance') == 'community-unverified'
            and row.get('structurally_valid') is True)


def _compatible(row, identity):
    return (isinstance(row, dict) and _admitted(row) and row.get("valid") is True
            and (row.get("full_start") is True or _bundled(row))
            and (identity.scope == "overall" or row.get("strategy") == identity.strategy)
            and (row.get("kind"), row.get("entity_key"), row.get("clock"), row.get("game_version"))
            == (identity.kind, identity.entity_key, identity.clock, identity.game_version)
            and (identity.kind != "segment" or identity.signature is not None
                 and row.get("signature") == identity.signature)
            and type(row.get("frames")) is int and 0 < row["frames"] <= 30 * 60 * 60 * 24
            and all(isinstance(row.get(key), str) and row[key].strip()
                    for key in ("id", "player_id", "player_name", "strategy"))
            and row.get("ghost") is not None)


def rank_recordings(rows, ranks, identity: PracticeIdentity) -> tuple[dict, ...]:
    """Regrade certified metadata; ignore claimed ranks and uncertified imports."""
    rows = _bounded(rows)
    if (ranks is None or identity.scope == "strategy" and not identity.strategy
            or identity.game_version not in ("us", "jp")):
        return ()
    with _read_context(ranks):
        key = rank_entity_key(identity)
        if ranks.clock_for(key) != identity.clock:
            return ()
        model = _rank_model(ranks, key, identity, identity.game_version)
        revision = getattr(ranks, "calibration_revision", None)
        result = {}
        for row in rows:
            if not _compatible(row, identity):
                continue
            progress = _progress(model, row["frames"], identity.scope)
            if progress is not None:
                result.setdefault(row["id"], {**row, **progress, "calibration_revision": revision,
                                              "current_strategy": identity.strategy, "scope": identity.scope})
    return tuple(result[key] for key in sorted(result))


def _checked_pins(pins):
    if not isinstance(pins, dict) or any(key not in {str(slot) for slot in range(MAX_RIVALS)}
                                        or not isinstance(value, str) or not value for key, value in pins.items()):
        raise ValueError("Choose a rival for an available slot.")
    if len(set(pins.values())) != len(pins):
        raise ValueError("Each rival can occupy one chosen slot.")
    return pins


def _ranked_row(row):
    return (isinstance(row, dict) and (row.get("manual_only") is True
            or _admitted(row) and row.get("valid") is True and (row.get("full_start") is True or _bundled(row)))
            and isinstance(row.get("id"), str)
            and type(row.get("frames")) is int and row["frames"] > 0
            and type(row.get("step")) is int and 0 <= row["step"] <= MAX_STEP
            and row.get("ghost") is not None)


def _range(pb, options):
    if options["mode"] == "fixed":
        return options["min_step"], options["max_step"]
    if pb is not None and not _valid_basis(pb):
        raise ValueError("The rival range needs a valid saved personal best.")
    # No saved PB starts at the floor; it does not override the chosen range.
    step = pb["step"] if pb is not None else 0
    return (min(MAX_STEP, step + max(1, options["min_ahead"])),
            min(MAX_STEP, step + max(1, options["max_ahead"])))


def _valid_basis(pb):
    return (isinstance(pb, dict) and type(pb.get("step")) is int and 0 <= pb["step"] <= MAX_STEP
            and type(pb.get("frames")) is int and pb["frames"] > 0)


def _same_as_pb(row, pb):
    if pb is None:
        return False
    if pb.get("id") == row["id"]:
        return True
    source = pb.get("source")
    # Only locally certified population rows carry admission-derived lineage.
    # Private manual imports cannot assert that they own a local saved run.
    if source is not None and row.get("manual_only") is not True and source == row.get("source"):
        return True
    origin = getattr(pb.get("ghost"), "origin", None)
    return origin is not None and origin == getattr(row.get("ghost"), "origin", None)


def _placed(row, slot, pinned):
    return {**row, "slot": slot, "pinned": pinned,
            "hue_degrees": ghost_hue(PB_HUE_DEGREES, slot + 1)}


def _missing_pin(identity, slot):
    return _placed({"id": identity, "player_name": "Chosen rival", "strategy": None,
                    "frames": None, "tier": None, "division": None, "step": None,
                    "ghost": None, "available": False,
                    "reason_text": "This chosen rival is unavailable. Reset or replace it."}, slot, True)


def _eligible_auto(row, pb, options, low, high, strategy, scope):
    # Different ROMs have different timing standards. A higher rank step
    # proves progress without comparing their raw clocks. At the capped final
    # step, a faster time only proves progress on the same ROM (or legacy PB).
    progresses = (pb is None or row["step"] > pb["step"]
                  or pb.get("game_version") in (None, row.get("game_version")) and row["frames"] < pb["frames"])
    return (row.get("manual_only") is not True and row.get("beaten_in_simulation") is not True
            and low <= row["step"] <= high
            and (options["mode"] == "fixed" or progresses)
            and (scope == "overall" or row.get("strategy") == strategy))


def player_key(row):
    """Who a recording belongs to; a recording without a player stands alone."""
    if row.get("_owned") is True:
        return ("current-player",)
    player = row.get("player_id")
    return player if isinstance(player, str) and player.strip() else ("recording", row["id"])


def _automatic(rows, count, low, high, players):
    """One seat per player, spread across the range.

    His rule (round 39): "per rival group, there should be only unique
    players." A player already seated (pinned or carried over) is never
    seated again automatically; a one-author population fills one seat.
    """
    winners = {}
    for row in rows:
        key = player_key(row), row["strategy"], row["step"]
        current = winners.get(key)
        if current is None or (row["frames"], row["id"]) < (current["frames"], current["id"]):
            winners[key] = row
    remaining = [row for row in winners.values() if player_key(row) not in players]
    picked = []
    count = min(count, len({player_key(row) for row in remaining}))
    for index in range(count):
        goal = low if count == 1 else low + (high - low) * index / (count - 1)
        row = min(remaining, key=lambda item: (abs(item["step"] - goal), item["step"], item["frames"], item["id"]))
        picked.append(row)
        players.add(player_key(row))
        remaining = [item for item in remaining if player_key(item) != player_key(row)]
    return sorted(picked, key=lambda row: (row["step"], row["frames"], row["id"]))


def _widened(candidates, taken, players, seats, high, strategy, scope, allow_fastest):
    """Seats the range could not fill: upward first, then downward.

    His rule (round 49): "(1) rivals within the user's default range ... (2)
    keep expanding the max rank until the rival list is full, (3) if that
    doesn't work, then expand downwards in the list." Upward takes the
    nearest subdivision above the range first; downward takes the nearest
    below first, including in-range runs that do not beat his PB. One seat
    per player. Remote rows join only with complete pool coverage, so a
    partial download never skips a nearer rival; bundled rows always may.
    """
    if seats <= 0:
        return []

    def open_row(row):
        # A developer climb retires the runs it has already beaten; they stay retired here.
        return (row["id"] not in taken and row.get("manual_only") is not True
                and row.get("beaten_in_simulation") is not True
                and (scope == "overall" or row.get("strategy") == strategy)
                and (allow_fastest or _bundled(row)))

    rows = [row for row in candidates.values() if open_row(row)]
    above = sorted((row for row in rows if row["step"] > high),
                   key=lambda row: (row["step"], -row["frames"], row["id"]))
    below = sorted((row for row in rows if row["step"] <= high),
                   key=lambda row: (-row["step"], row["frames"], row["id"]))
    picked = []
    for row in (*above, *below):
        if len(picked) == seats:
            break
        if player_key(row) not in players:
            picked.append(row)
            players.add(player_key(row))
    return picked


def select_rivals(rows, pb, options, pins: dict[str, str], previous=(), *, scope="strategy", allow_fastest=True) -> tuple[dict, ...]:
    """Choose visible slots; caller retains hidden pins and owns PB playback.

    Pass the previous roster when changing one manual choice, to preserve its
    other compatible automatic slots. Omit it when the saved basis/range changes.
    Seats the range cannot fill widen upward, then downward (`_widened`),
    including recordings already beaten by the PB. Missing chosen recordings
    remain occupied rather than silently replaced.
    Overall permits other strategies, while every row keeps its recorded label.
    """
    scope = _checked_scope(scope)
    options, pins = checked_options(options), _checked_pins(pins)
    candidates = {row["id"]: row for row in _bounded(rows) if _ranked_row(row) and row.get("scope", scope) == scope}
    previous = tuple(row for row in _bounded(previous) if _ranked_row(row))
    low, high = _range(pb, options)
    strategy = pb.get("current_strategy") if pb else None
    if strategy is None:
        strategy = next((row.get("current_strategy") for row in candidates.values()
                         if row.get("current_strategy") is not None), None)
    if scope == "strategy":
        candidates = {key: row for key, row in candidates.items() if strategy is not None and row.get("strategy") == strategy}
    used = {row["id"] for row in candidates.values()
            if options["include_pb"] and _same_as_pb(row, pb)}
    result = {}
    players = set()
    for slot in range(options["count"]):
        identity = pins.get(str(slot))
        if identity is not None:
            row = candidates.get(identity)
            duplicate = row is not None and player_key(row) in players
            if row is not None and identity not in used and not duplicate:
                result[slot] = _placed(row, slot, True)
                players.add(player_key(row))
            else:
                result[slot] = _missing_pin(identity, slot)
                if duplicate:
                    result[slot]["reason_text"] = "This player already has a chosen rival. Reset or replace this slot."
            used.add(identity)
    # Persisted conflicting pins keep the first playable slot. Every seat,
    # including protected choices and carried-over rows, belongs to one player.
    for old in sorted(previous, key=lambda item: item.get("slot") if type(item.get("slot")) is int else -1):
        slot = old.get("slot")
        row = candidates.get(old["id"])
        if (not old.get("pinned") and type(slot) is int and 0 <= slot < options["count"] and slot not in result
                and row is not None and row["id"] not in used and player_key(row) not in players
                and _eligible_auto(row, pb, options, low, high, strategy, scope)):
            result[slot] = _placed(row, slot, False)
            used.add(row["id"])
            players.add(player_key(row))
    available = [row for row in candidates.values() if row["id"] not in used
                 and _eligible_auto(row, pb, options, low, high, strategy, scope)]
    automatic = list(_automatic(available, options["count"] - len(result), low, high, players))
    automatic += _widened(candidates, used | {row["id"] for row in automatic}, players,
                          options["count"] - len(result) - len(automatic), high, strategy, scope, allow_fastest)
    automatic = iter(sorted(automatic, key=lambda row: (row["step"], row["frames"], row["id"])))
    for slot in range(options["count"]):
        if slot not in result and (row := next(automatic, None)) is not None:
            result[slot] = _placed(row, slot, False)
    return tuple(result[slot] for slot in sorted(result))


def add_slot(roster, count: int, replacement_slot: int | None = None) -> int:
    """Add uses a vacancy or slowest automatic slot; direct replacement is explicit."""
    if type(count) is not int or not 1 <= count <= MAX_RIVALS:
        raise ValueError("Choose how many rivals to show.")
    if replacement_slot is not None:
        if type(replacement_slot) is not int or not 0 <= replacement_slot < count:
            raise ValueError("Choose a visible rival to replace.")
        return replacement_slot
    rows = {row["slot"]: row for row in _bounded(roster)
            if type(row.get("slot")) is int and 0 <= row["slot"] < count}
    empty = next((slot for slot in range(count) if slot not in rows), None)
    if empty is not None:
        return empty
    automatic = [row for row in rows.values() if not row.get("pinned") and type(row.get("frames")) is int]
    if not automatic:
        raise ValueError("Every rival is chosen by you. Select a rival to replace it.")
    return min(automatic, key=lambda row: (-row["frames"], row["slot"]))["slot"]
