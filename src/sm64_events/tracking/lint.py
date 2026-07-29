"""Author-time lint for segment definitions (spec 2026-07-28-multi-step-
segments, Task 15). Pure — no server, no db, no I/O.

Every rule here corresponds to a bug that shipped and cost a live report.
`can_run_from`, `_feed_waypoint`, and the module docstring in
`tracking/segments.py` document the FSM invariants these traps come from;
this module turns four of them into checks an author sees before saving,
instead of finding out from a stuck "Running" chip days later.

`lint_definition(d, all_defs)` returns `list[{"rule", "severity", "message"}]`,
severity `"error"` (the definition CANNOT work — reject or fix before
saving) or `"warning"` (a real risk under a specific scenario, not a
guaranteed failure — advise, don't block). One function per rule, registered
in `RULES` so a new rule is one row; `lint_definition` just concatenates every
rule's findings for one definition.

Rules:
  unfireable (error) — start and the def's own NEXT required step (its first
      waypoint, or its end trigger when it has none) are satisfiable by the
      SAME `level_changed` event. SegmentEngine.feed only runs the CLOSURE
      handler for an already-armed def (segments.py:_feed_waypoint's own
      docstring, and feed()'s `if arm is not None:` gate) — a not-yet-armed
      def can only ARM on an event, never also close/advance on it, so if the
      one event that arms it is the ONLY event that could ever satisfy its
      next step, the def hangs armed forever. Shipped as `seg:ddd->bitfs`
      (DDD exit -> BitFS entry, the world's old 23->19 one-way edge) and
      surfaced as a segment stuck "running" in an unrelated course (live
      report 2026-07-24). That exact edge was removed from
      `memory.addresses.WORLD_EDGES_ONE_WAY` on 2026-07-27 for being wrong
      (BitFS is entered from the castle basement, two level_changed events
      apart) — so this rule's own OWN mutation fixture uses a pair the
      current topology actually connects directly instead (HMC exit -> the
      castle, `((6, 3), 7)`, verified against `world_connections()` below),
      not the historical DDD/BitFS shape, which no longer reproduces the bug.

  start_looser_than_waypoint (warning) — a start trigger that doesn't pin a
      param one of the def's own waypoint clauses does, for the same clause
      TYPE. `_feed_waypoint`'s authoring caveat: a major-action cancel (a
      misroute — the player crossed the wrong edge) disarms the def, and the
      SAME event is then re-evaluated against start_triggers in the same
      feed() call; a start trigger loose enough to also match that misroute
      re-arms the segment on the spot instead of cleanly cancelling. Only a
      WARNING, not an error: the happy path (the player actually takes the
      waypoint's specific route) never triggers it, so a def with this shape
      still works most of the time — it just silently swallows one class of
      misroute instead of recording it as abandoned. Exempt when
      `d.match_mode == "loose"`: `feed()` routes a loose def to `_feed_loose`
      regardless of waypoints, and `_feed_loose` has no major-action cancel at
      all — an off-sequence event is transparent, not a disarm-and-re-arm —
      so the race this rule warns about cannot occur there (see the guard in
      `_rule_start_looser_than_waypoint` below).

  unrunnable_arm_position (error) — `can_run_from` is False at EVERY start
      trigger's own `arm_level`. Live report 2026-07-27: a Usamune menu warp
      fabricated WF -> Cool, Cool Mountain (one `level_changed` 24 -> 5), and
      "WF -> SSL" armed with Mario standing in CCM — nothing disarms a def
      whose player then stays put, so it read ACTIVE SEGMENT / Running for
      six minutes of CCM practice. `can_run_from` (segments.py) is the runtime
      gate that already refuses that specific arm; this rule instead asks the
      author-time question `can_run_from` cannot: is there ANY position,
      including the def's own INTENDED one, from which it could ever run to
      completion? Only start triggers with a concrete, deterministic
      `arm_level` (attempt_anchor, spawned, area_enter, or a level_exit/
      level_enter that pins `to`) are checked — the majority of seeded
      `level_exit` clauses omit `to` (arm_level None, "could land anywhere"),
      and the codebase-wide convention is unknown-means-yes: a clause we
      cannot pin a position for cannot be proven broken, so it always rescues
      the definition, exactly as `can_run_from(d, clause, level=None)` itself
      returns True. This rule fires only when EVERY start clause is BOTH
      concrete and unrunnable there — a structurally broken def, not merely
      one vulnerable to a menu-warp arm elsewhere (which `can_run_from`
      already handles at runtime, correctly, for every seeded movement).

  duplicate (warning) — another definition (by id, in `all_defs`) shares this
      one's start_triggers, end_triggers, waypoints AND guards. Not an error:
      a duplicate isn't broken, just redundant, and match_mode is
      deliberately excluded from the comparison (a loose and a strict version
      of literally the same steps are a real, if unusual, authoring choice —
      not the same bug two authoring passes produced).

A NOTE ON WORLD TOPOLOGY (read before "simplifying" `unfireable`): segments.py
warns, repeatedly, against deriving checks from `WORLD_EDGES_TWO_WAY`/
`_ONE_WAY` — "a stored def must keep matching fabricated edges, and a check
derived from that table could only be tested against the table it came from."
That warning is about `can_run_from`, a HARD ARM GATE that runs at REPLAY
time and must keep matching whatever edge the Usamune warp menu invents,
forever, for every historical journal. A lint finding is a different
contract: it advises an AUTHOR at edit time, once, before the definition is
saved — it never refuses to match an event, and it is not re-evaluated
against old journals when the topology table changes. Using
`world_connections()` for advice here is doing a different job than using it
to gate arming would be, which is why `unfireable` is allowed to import it
where `can_run_from` is not. Deleting this rule because of that warning
removes the only check that catches an unfireable direct-edge pair before it
ships — see the `unfireable` docstring above for exactly that regression.
"""
from typing import Callable

from sm64_events.memory.addresses import node_key, world_connections
from sm64_events.tracking.segments import TRIGGERS, arm_level, can_run_from


def _direct_edge(from_level: int, from_subarea: int | None,
                 to_level: int, to_subarea: int | None) -> bool:
    """Does the world connect (from_level, from_subarea) straight to
    (to_level, to_subarea) in ONE edge -- i.e. could a single level_changed
    produce this exact from->to? `world_connections()` is the same successor
    map the segment builder's dropdown filtering uses (tracking/segments.py);
    this only ever asks a ONE-HOP question, so it reads that map directly
    rather than building any graph/BFS structure of its own."""
    for dest_level, dest_area in world_connections().get(
            node_key(from_level, from_subarea), []):
        if dest_level != to_level:
            continue
        if to_subarea is not None and dest_area != to_subarea:
            continue
        return True
    return False


def _one_event_could_satisfy_both(start_clause: dict, next_clause: dict) -> bool:
    """Could ONE level_changed event match both `start_clause` (a level_exit)
    and `next_clause` (a level_enter)? Only these two types combine into a
    single level_changed; every other trigger type fires on a different event
    kind entirely and so can never collide with a level_exit this way.

    `from` (required by validate_definition) and `to` (level_enter's own
    required destination) are read with `.get`, not `[...]` -- Task 16 found
    this raising `KeyError` when either is still unset: `POST /api/segments/
    lint` runs this rule against the editor's CURRENT, possibly in-progress
    form on every edit (server/api.py's own docstring says so explicitly),
    and a just-added clause with no level picked yet is exactly that shape,
    not a hypothetical. An unset required param here is unknowable, not
    absent-on-purpose, so it is treated the same as every other "can't prove
    a collision" case in this module: rescue the definition (return False)
    rather than crash."""
    if start_clause.get("type") != "level_exit" \
            or next_clause.get("type") != "level_enter":
        return False
    next_to = next_clause.get("to")
    if next_to is None:
        return False
    to = start_clause.get("to")
    if to is not None:
        # The exit clause already pins its own destination (e.g. MIPS Clip) --
        # no topology needed, the match lambdas require ev.payload["to"] to be
        # exactly this value, so compare it directly.
        if to != next_to:
            return False
        to_subarea = next_clause.get("to_subarea")
        return to_subarea is None or start_clause.get("to_subarea") == to_subarea
    from_level = start_clause.get("from")
    if from_level is None:
        return False
    return _direct_edge(from_level, start_clause.get("from_subarea"),
                        next_to, next_clause.get("to_subarea"))


def _rule_unfireable(d, all_defs) -> list[dict]:
    next_required = d.waypoints[0] if d.waypoints else d.end_triggers
    for start_clause in d.start_triggers:
        for next_clause in next_required:
            if _one_event_could_satisfy_both(start_clause, next_clause):
                return [{
                    "rule": "unfireable",
                    "severity": "error",
                    "message": (
                        f"start {start_clause} and "
                        f"{'waypoint 1' if d.waypoints else 'end'} clause "
                        f"{next_clause} are satisfiable by the SAME "
                        "level_changed event (the world connects them "
                        "directly) -- SegmentEngine only closes an "
                        "ALREADY-armed def, so this def would arm on that "
                        "event and hang \"Running\" forever (live report "
                        "2026-07-24, DDD -> BitFS). Start the segment on an "
                        "earlier event instead."),
                }]
    return []


def _rule_start_looser_than_waypoint(d, all_defs) -> list[dict]:
    # A loose def never runs _feed_waypoint -- feed() routes match_mode ==
    # "loose" to _feed_loose regardless of waypoints (segments.py's own
    # docstring: "a loose def owns its own waypoint progression... whether or
    # not it carries waypoints"). _feed_loose has no major-action cancel: an
    # off-sequence star/key grab or level crossing is fully transparent (no
    # disarm, no re-arm) -- "EVERYTHING ELSE IS TRANSPARENT... the whole
    # feature". The cancel-and-re-arm race this rule warns about is specific
    # to _feed_waypoint's silent-cancel branch, which a loose def can never
    # reach, so warning on one here would cite a mechanism that cannot fire.
    if d.match_mode == "loose":
        return []
    findings = []
    for step in d.waypoints:
        for waypoint_clause in step:
            spec = TRIGGERS.get(waypoint_clause.get("type"))
            if spec is None:
                continue
            for start_clause in d.start_triggers:
                if start_clause.get("type") != waypoint_clause.get("type"):
                    continue
                missing = sorted(
                    name for name in spec.params
                    if waypoint_clause.get(name) is not None
                    and start_clause.get(name) is None)
                if missing:
                    findings.append({
                        "rule": "start_looser_than_waypoint",
                        "severity": "warning",
                        "message": (
                            f"start trigger {start_clause} doesn't pin "
                            f"{missing}, which waypoint clause "
                            f"{waypoint_clause} does -- a misroute event "
                            "that cancels the waypoint sequence can still "
                            "satisfy this looser start trigger and silently "
                            "re-arm in the same tick instead of recording an "
                            "abandoned attempt (SegmentEngine._feed_waypoint's "
                            "authoring caveat). Pin the same params on the "
                            "start trigger."),
                    })
    return findings


def _rule_unrunnable_arm_position(d, all_defs) -> list[dict]:
    for clause in d.start_triggers:
        level = arm_level(clause)
        if level is None or can_run_from(d, clause, level):
            return []   # this clause offers (or might offer -- unknown means
                        # yes) a position the def can actually be run from
    if not d.start_triggers:
        return []
    return [{
        "rule": "unrunnable_arm_position",
        "severity": "error",
        "message": (
            "every start trigger arms at a position can_run_from refuses "
            "(checked at each clause's own arm_level) -- this segment can "
            "never be run to completion no matter how it's armed (live "
            "report 2026-07-27, WF -> SSL armed inside Cool, Cool "
            "Mountain). Start the segment somewhere its own next required "
            "step can actually fire from."),
    }]


def _rule_duplicate(d, all_defs) -> list[dict]:
    for other in all_defs:
        if other.id == d.id:
            continue
        if (other.start_triggers == d.start_triggers
                and other.end_triggers == d.end_triggers
                and other.waypoints == d.waypoints
                and other.guards == d.guards):
            return [{
                "rule": "duplicate",
                "severity": "warning",
                "message": (
                    f"duplicates definition {other.id} ({other.name!r}) -- "
                    "identical start/end/waypoints/guards."),
            }]
    return []


RULES: dict[str, Callable] = {
    "unfireable": _rule_unfireable,
    "start_looser_than_waypoint": _rule_start_looser_than_waypoint,
    "unrunnable_arm_position": _rule_unrunnable_arm_position,
    "duplicate": _rule_duplicate,
}


def lint_definition(d, all_defs: list) -> list[dict]:
    """Every finding from every rule in `RULES`, for one definition. `all_defs`
    is whatever this definition should be checked against for cross-def rules
    (duplicate) -- it may or may not include `d` itself (rules that need to
    exclude self-comparison do it by id, so either is safe to pass)."""
    findings = []
    for rule in RULES.values():
        findings.extend(rule(d, all_defs))
    return findings
