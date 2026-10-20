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
from sm64_events.ranks import classify, curves, region_scope, scoring
from sm64_events.ranks.calibration import resolve_curve
from sm64_events.ranks.standards import entity_key

MAX_RIVALS = 4
MAX_CANDIDATES = 16384
MAX_AHEAD = 10
MAX_STEP = scoring.progression_key(classify.RANK_NAMES[0], scoring.DIVISION_NUMERALS[-1])
NO_PB_TOP_STEP = 2 * scoring.DIVISIONS_PER_TIER - 1
"""With no saved PB, rivals spread across the two lowest tiers (Capless V to Toad I)."""
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
    # Which ROMs' recordings the providers FETCH (`ranks/region_scope.py`):
    # "regional" only `game_version`'s, "shared" both. Whether one may SERVE
    # is never read from here: `ghost_region_scope` asks the standards, so a
    # default-built identity cannot widen what plays.
    region_scope: str = region_scope.REGIONAL

    def __post_init__(self):
        _checked_scope(self.scope)
        region_scope.checked(self.region_scope)


def rank_entity_key(identity: PracticeIdentity) -> str:
    """Adapt the ghost key through the rank store's canonical key builder."""
    if identity.kind == "segment":
        return entity_key(None, None, int(identity.entity_key))
    if identity.kind == "star":
        course, star = map(int, identity.entity_key.split("-"))
        return entity_key(course, star)
    raise ValueError("A ghost belongs to a star or segment.")


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
        raise ValueError("Choose valid ghost settings.")
    if "strategy_only" in value and type(value["strategy_only"]) is not bool:
        raise ValueError("Choose valid ghost settings.")
    # Old persisted drafts keep their other choices; every strategy now has its
    # own rivals, so the former opt-in field no longer controls selection.
    result = DEFAULT_OPTIONS | {key: item for key, item in value.items() if key != "strategy_only"}
    if result["mode"] not in ("adaptive", "fixed"):
        raise ValueError("Choose how the ghost range follows your practice.")
    if type(result["include_pb"]) is not bool:
        raise ValueError("Choose valid ghost settings.")
    limits = {"count": (1, MAX_RIVALS), "min_ahead": (0, MAX_AHEAD), "max_ahead": (0, MAX_AHEAD),
              "min_step": (0, MAX_STEP), "max_step": (0, MAX_STEP)}
    for key, (low, high) in limits.items():
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError("Choose a ghost range within the available ranks.")
    if result["min_ahead"] > result["max_ahead"] or result["min_step"] > result["max_step"]:
        raise ValueError("Minimum rank cannot exceed maximum rank.")
    return result


def _bounded(rows):
    result = tuple(islice(rows, MAX_CANDIDATES + 1))
    if len(result) > MAX_CANDIDATES:
        raise ValueError("This ghost metadata snapshot exceeds its limit.")
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


def recorded_version(row):
    """The ROM a ghost was recorded on; None when nobody knows. A bundled row's
    `game_version` is only the query's context (`ghost/bundled_beta.py`)."""
    return row.get("recorded_game_version") if _bundled(row) else row.get("game_version")


def ghost_region_scope(identity, strategy, ranks):
    """The region scope that judges one ghost: its strategy's on this target,
    from the standards (the practice's own strategy on the Strategy tab)."""
    if identity.scope == "strategy":
        strategy = identity.strategy
    return region_scope.scope_of(rank_entity_key(identity), strategy, ranks)


def region_serves(identity, strategy, recorded, ranks) -> bool:
    """Whether a ghost of `strategy` recorded on `recorded` may serve this practice."""
    return region_scope.serves(ghost_region_scope(identity, strategy, ranks),
                               identity.game_version, recorded)


def _compatible(row, identity, scope):
    return (isinstance(row, dict) and _admitted(row) and row.get("valid") is True
            and (row.get("full_start") is True or _bundled(row))
            and (identity.scope == "overall" or row.get("strategy") == identity.strategy)
            and (row.get("kind"), row.get("entity_key"), row.get("clock"))
            == (identity.kind, identity.entity_key, identity.clock)
            # Only a bundled historical recording may lack its ROM; any other
            # admitted row without one is malformed, whatever the scope.
            and (_bundled(row) or row.get("game_version") in region_scope.VERSIONS)
            and region_scope.serves(scope, identity.game_version, recorded_version(row))
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
            scope = ghost_region_scope(identity, row.get("strategy"), ranks) if isinstance(row, dict) else None
            if not _compatible(row, identity, scope):
                continue
            progress = _progress(model, row["frames"], identity.scope)
            if progress is not None:
                result.setdefault(row["id"], {**row, **progress, "calibration_revision": revision,
                                              "current_strategy": identity.strategy, "scope": identity.scope,
                                              "region_scope": scope})
    return tuple(result[key] for key in sorted(result))


def _checked_pins(pins):
    if not isinstance(pins, dict) or any(key not in {str(slot) for slot in range(MAX_RIVALS)}
                                        or not isinstance(value, str) or not value for key, value in pins.items()):
        raise ValueError("Choose a ghost for an available slot.")
    if len(set(pins.values())) != len(pins):
        raise ValueError("Each ghost can occupy one chosen slot.")
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
        raise ValueError("The ghost range needs a valid saved personal best.")
    if pb is None:
        # Nothing to be ahead of yet: show the bottom two tiers, spread out.
        return 0, NO_PB_TOP_STEP
    step = pb["step"]
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
    return _placed({"id": identity, "player_name": "Chosen ghost", "strategy": None,
                    "frames": None, "tier": None, "division": None, "step": None,
                    "ghost": None, "available": False,
                    "reason_text": "This chosen ghost is unavailable. Reset or replace it."}, slot, True)


def _ahead(row, pb):
    """Whether the PB has not passed this recording yet.

    Different ROMs have different timing standards. A higher rank step
    proves progress without comparing their raw clocks. At the capped final
    step, a faster time only proves progress on the same ROM (or legacy PB),
    or when this strategy's region scope says both ROMs time it alike.
    """
    if pb is None:
        return True
    comparable = (pb.get("game_version") in (None, row.get("game_version"))
                  or row.get("region_scope") == region_scope.SHARED)
    return row["step"] > pb["step"] or comparable and row["frames"] < pb["frames"]


def _automatic_row(row, strategy, scope):
    return (row.get("manual_only") is not True
            and (scope == "overall" or row.get("strategy") == strategy))


def _eligible_auto(row, pb, options, low, high, strategy, scope):
    return (_automatic_row(row, strategy, scope) and low <= row["step"] <= high
            and (options["mode"] == "fixed" or _ahead(row, pb)))


def _keeps_seat(row, pb, options, low, high, strategy, scope):
    """A seated rival stays until the PB passes it (his rule, 2026-10-16:
    seats keep their player and colour), even below the range's floor.

    A seat above the range's top is dealt again: widening re-seats it in the
    same slot while the range stays empty, and an undone PB brings nearer
    rivals back.
    """
    if options["mode"] == "fixed":
        return _eligible_auto(row, pb, options, low, high, strategy, scope)
    return _automatic_row(row, strategy, scope) and _ahead(row, pb) and row["step"] <= high


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
        return (row["id"] not in taken and row.get("manual_only") is not True
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

    Pass the previous roster to keep its seats: an automatic seat keeps its
    player, slot and colour until the PB passes it, then takes that player's
    faster in-range recording when one exists. Omit it to deal afresh (the
    rival settings changed). With no saved PB the range spreads across the two
    lowest tiers. Seats the range cannot fill widen upward, then downward (`_widened`),
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
    players = set()
    result = _place_pins(pins, candidates, options["count"], used, players)
    bounds = dict(pb=pb, options=options, low=low, high=high, strategy=strategy, scope=scope)
    # Persisted conflicting pins keep the first playable slot. Every seat,
    # including protected choices and carried-over rows, belongs to one player.
    seats = {old["slot"]: old for old in previous
             if not old.get("pinned") and type(old.get("slot")) is int and 0 <= old["slot"] < options["count"]}
    _carry_seats(seats, candidates, result, used, players, bounds)
    available = [row for row in candidates.values() if row["id"] not in used and _eligible_auto(row, **bounds)]
    automatic = list(_automatic(available, options["count"] - len(result), low, high, players))
    automatic += _widened(candidates, used | {row["id"] for row in automatic}, players,
                          options["count"] - len(result) - len(automatic), high, strategy, scope, allow_fastest)
    _deal(automatic, result, seats, options["count"])
    return tuple(result[slot] for slot in sorted(result))


def _place_pins(pins, candidates, count, used, players):
    """Chosen rivals first: each keeps its slot, playable or not."""
    result = {}
    for slot in range(count):
        identity = pins.get(str(slot))
        if identity is None:
            continue
        row = candidates.get(identity)
        duplicate = row is not None and player_key(row) in players
        if row is not None and identity not in used and not duplicate:
            result[slot] = _placed(row, slot, True)
            players.add(player_key(row))
        else:
            result[slot] = _missing_pin(identity, slot)
            if duplicate:
                result[slot]["reason_text"] = "This player already has a chosen ghost. Reset or replace this slot."
        used.add(identity)
    return result


def _carry_seats(seats, candidates, result, used, players, bounds):
    """Keep each previous automatic seat the PB has not passed, in its slot.

    Same rival, faster: a seat the new PB passed first takes its player's
    fastest in-range recording that is still ahead (his design, 2026-10-16).
    """
    def seat(row, slot):
        result[slot] = _placed(row, slot, False)
        used.add(row["id"])
        players.add(player_key(row))

    passed = []
    for slot, old in sorted(seats.items()):
        row = candidates.get(old["id"])
        if slot in result or row is not None and (row["id"] in used or player_key(row) in players):
            continue
        if row is not None and _keeps_seat(row, **bounds):
            seat(row, slot)
        elif not _ahead(row or old, bounds["pb"]):
            passed.append((slot, old))
    for slot, old in passed:
        player = player_key(old)
        faster = [row for row in candidates.values() if row["id"] not in used and player_key(row) == player
                  and player not in players and _eligible_auto(row, **bounds)]
        if faster:
            seat(min(faster, key=lambda row: (row["frames"], row["id"])), slot)


def _deal(automatic, result, seats, count):
    """Fill vacancies in rank order; a returning player takes back the slot (and colour) they held."""
    automatic = sorted(automatic, key=lambda row: (row["step"], row["frames"], row["id"]))
    held = {player_key(old): slot for slot, old in seats.items()}
    for row in tuple(automatic):
        slot = held.get(player_key(row))
        if slot is not None and slot not in result:
            result[slot] = _placed(row, slot, False)
            automatic.remove(row)
    remaining = iter(automatic)
    for slot in range(count):
        if slot not in result and (row := next(remaining, None)) is not None:
            result[slot] = _placed(row, slot, False)


def add_slot(roster, count: int, replacement_slot: int | None = None) -> int:
    """Add uses a vacancy or slowest automatic slot; direct replacement is explicit."""
    if type(count) is not int or not 1 <= count <= MAX_RIVALS:
        raise ValueError("Choose how many ghosts to show.")
    if replacement_slot is not None:
        if type(replacement_slot) is not int or not 0 <= replacement_slot < count:
            raise ValueError("Choose a visible ghost to replace.")
        return replacement_slot
    rows = {row["slot"]: row for row in _bounded(roster)
            if type(row.get("slot")) is int and 0 <= row["slot"] < count}
    empty = next((slot for slot in range(count) if slot not in rows), None)
    if empty is not None:
        return empty
    automatic = [row for row in rows.values() if not row.get("pinned") and type(row.get("frames")) is int]
    if not automatic:
        raise ValueError("Every ghost is chosen by you. Select a ghost to replace it.")
    return min(automatic, key=lambda row: (-row["frames"], row["slot"]))["slot"]
