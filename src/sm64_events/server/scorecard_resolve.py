"""The Scorecard's goal resolution, shared by every door that shows a goal.

`GoalResolver` turns the saved choice (`scorecard_goal`: one rank plus any
players and custom sets), the per-entry pins (`scorecard_entry_goals`) and
your PBs into one goal per entry. `resolve_entries` is the ONE derivation the
card's tiles and `GET /api/scorecard/entry_goals` both read (project rule 6),
so a Practice card and its Scorecard tile cannot disagree. Lifted out of
`scorecard_api.create_scorecard_router` when per-entry goals landed -- the
seam `docs/architecture.md` named for the four goal resolvers.
"""
import re
from datetime import datetime, timezone

from fastapi import HTTPException

from sm64_events.core.timefmt import attainable_cs, format_igt_short, frame_at_or_after
from sm64_events.library.ratings import runner_times
from sm64_events.ranks.classify import RANK_NAMES, display_cs
from sm64_events.memory.addresses import star_name
from sm64_events.ranks.scorecard import (
    SPECIAL_STAR_LABELS, automatic_goal, card_keys, division_goal_cs,
    entry_automatic_goal)
from sm64_events.ranks.scoring import DIVISION_NUMERALS
from sm64_events.server.ranks_api import _score_scope
from sm64_events.server.scorecard_goals import combined, sources_of, valid_division
from sm64_events.server.scorecard_standards import tile_strategies
from sm64_events.tracking.activestrat import ActiveStrats
from sm64_events.tracking.goal_progress import entry_key_of, goal_progress, parse_utc
from sm64_events.tracking.views import fastest_current_pbs

GOAL_KEY = "scorecard_goal"
# Named hand-authored goals -- {name: {entity_key: goal_cs}}. A division or
# runner goal RESOLVES per entity every request; a custom one is typed once
# (round-tripped through the card's own live edit, never re-derived) and
# simply looked up here, which is why it needs no resolver of its own below.
CUSTOM_KEY = "scorecard_custom_goals"
# Per-entry PINS -- {entity_key: goal} set from a Practice card. A pin REPLACES
# the fastest-offer merge for that one entry (never joins it); clearing it
# returns the entry to the normal resolution. Per entity, so every scope reads
# the same pin. One threshold each: a division on the entry's ladder, a
# runner's time on it, or a typed time (`{"kind": "time", "cs"}`). A pin may
# also ask for a REPEAT: `count` finishes under it (1..999), consecutive when
# `streak`; `set_at` (UTC) is when its threshold or repeat last changed, which
# is where its progress window opens (`tracking/goal_progress.py`).
ENTRY_GOALS_KEY = "scorecard_entry_goals"
# {entity_key: [attempt ids]} the player forgave: an attempt the tracker
# judged (truthfully) as a streak-breaker that he considers fair game. His
# words: "Maybe we have a button to undo / restore the streak, just in case we
# automatically detected their reset (truthfully), but the player wants to
# keep the streak because they consider it fair game".
FORGIVEN_KEY = "scorecard_goal_forgiven"
MAX_REPEAT = 999
ENTRY_KEY = re.compile(r"^(star:\d+:\d+|segment:\d+)$")


def require_entry_key(key: str) -> None:
    """422 unless `key` is a star or segment entity key."""
    if not ENTRY_KEY.match(key):
        raise HTTPException(422, f"not an entry key: {key!r}")
# Which ROM regions a RUNNER goal is allowed to offer a time from -- round 24,
# his mechanism verbatim: "we should define their scorecard based on their
# detected region (e.g., US in my case). BUT it should be a deliberate choice
# ... There must be at least one region enabled at all times. This gives 3
# valid states: US only, JP only, or JP and US combined. When combined for the
# scorecard, we simply take the faster time across both regions."
#
# Server-side beside the goal for the same reason `GOAL_KEY` is: two clients
# (browser + desktop GUI) reading one choice. ABSENT means "follow the
# detected region", which is not the same as `["us"]` -- an untouched
# scorecard keeps tracking `ranks.grading_version` as a mode flip or a
# detection moves it, and only a deliberate pick freezes it.
REGIONS_KEY = "scorecard_regions"
VALID_REGIONS = ("us", "jp")


def pin_of(goal) -> dict | None:
    """A validated, normalized pin (without `set_at`), or a 422. `None`
    clears. Every pin kind is a fixed threshold, so any may repeat."""
    if goal is None:
        return None
    count, streak = goal.get("count", 1), goal.get("streak", False)
    if (not isinstance(count, int) or isinstance(count, bool)
            or not 1 <= count <= MAX_REPEAT):
        raise HTTPException(422, f"count is a whole number from 1 to {MAX_REPEAT}")
    if not isinstance(streak, bool):
        raise HTTPException(422, "streak is true or false")
    return {**_threshold_of(goal), "count": count, "streak": streak}


def _threshold_of(goal) -> dict:
    kind = goal.get("kind")
    if kind == "division":
        if not valid_division(goal.get("tier"), goal.get("division")):
            raise HTTPException(422, "unknown tier/division")
        return {"kind": kind, "tier": goal["tier"], "division": goal["division"]}
    if kind == "runner":
        name = goal.get("runner")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(422, "a runner goal needs a name")
        return {"kind": kind, "runner": name}
    if kind == "time":
        cs = goal.get("cs")
        if not isinstance(cs, int) or isinstance(cs, bool) or cs <= 0:
            raise HTTPException(422, "a time goal is positive centiseconds")
        return {"kind": kind, "cs": attainable_cs(cs)}
    raise HTTPException(422, f"unknown goal kind {kind!r}")


def _time_label(cs: int) -> str:
    """A typed goal's name: the time as the timer shows it (a minute-less
    time drops its `0'`, the UI's own short form), or `sub 1'45"` for a
    whole-second value."""
    text = format_igt_short(frame_at_or_after(cs))
    return f"sub {text[:-2]}" if cs % 100 == 0 else text


def entry_source(key: str, resolved: dict) -> dict:
    """Which source set this entry's goal: its pin, else the Scorecard
    source that won (the rank source when nothing offered a time). A
    rank's `label` is the tier KEY ("Bronze I"); cap display names live
    in `ui/components/caps.js` alone, so clients draw from tier/division."""
    pin = resolved["pins"].get(key)
    source = pin if pin is not None else resolved["sources"][resolved["owner"].get(key, 0)]
    kind = source["kind"]
    if kind == "time":
        return {"kind": "time", "label": _time_label(source["cs"])}
    if kind == "runner":
        return {"kind": "runner", "label": source["runner"], "runner": source["runner"]}
    if kind == "custom":
        return {"kind": "custom", "label": source["name"], "name": source["name"]}
    target = source if pin is not None else resolved["targets"].get(key) or source
    tier, division = target.get("tier"), target.get("division")
    return {"kind": kind, "tier": tier, "division": division,
            "label": f"{tier} {division}" if tier is not None else ""}



def entry_goal(key: str, clock: str, resolved: dict) -> dict:
    """One entry's EntryGoal. A repeat pin (count > 1) carries `progress`
    (`resolved["progress"]`, filled by `GoalResolver.entry_progress`) and is
    achieved once its best run reaches the count; otherwise achieved means
    your PB is at or under the goal."""
    you_cs = resolved["you"].get(key)
    goal_cs = resolved["goal"].get(key)
    pin = resolved["pins"].get(key) or {}
    count, streak = pin.get("count", 1), pin.get("streak", False)
    progress = resolved.get("progress", {}).get(key) if count > 1 else None
    achieved = (progress["best"] >= progress["needed"] if progress is not None
                else you_cs is not None and goal_cs is not None and you_cs <= goal_cs)
    return {"key": key, "clock": clock, "you_cs": you_cs, "goal_cs": goal_cs,
            "source": entry_source(key, resolved),
            "pinned": key in resolved["pins"],
            "count": count, "streak": streak, "progress": progress,
            "achieved": achieved}


class GoalResolver:
    """The goal closures `create_scorecard_router` used to hold, over one
    service and one Library snapshot. One instance per router: it owns the
    runner-times memo below."""

    def __init__(self, service, library=None, adoptions=None):
        self.service = service
        self.library = library
        self.adoptions = adoptions
        self._runner_times_memo: dict = {}

    def your_times(self, keys: list[str], regions: list[str], *, pb_rows=None) -> dict[str, int]:
        """Your FASTEST current PB per key across every strategy and every
        ROM in `regions` (the card's own region setting, the same list a
        runner goal offers times from), on the entity's own clock -- igt for
        a star, rta for a movement -- as displayed centiseconds. A key with
        no saved PB is simply absent, which is the "no goal on this tile"
        case `_tile` already handles.

        Fastest, not latest (round 33, 2026-09-05): the strategy-blind
        `current_pb` answers the latest SAVE across strategies, and an
        import lands a star's rows in sheet order, so RONC3NA's own column
        imported onto an empty log showed 11"50 (Left side TJ, landed
        second) against a goal of 11"26 (his Standard, landed first) --
        "I literally am ronc3na in this case. Both should automatically be
        matching." A runner goal offers the runner's fastest row, so YOU is
        the same quantity for him.

        And across ROMs (round 34, same day): a merged (JP)/(US) sheet row
        lands both of a runner's times under ONE strategy, and keyed by
        strategy alone the later (US) row shadowed the faster JP one -- WDW
        and TTC's 100-coin tiles read +0.73 and +1.30 against his own
        column. `regions` is the merge the goal side already runs."""
        rows = self.service.db.pbs() if pb_rows is None else pb_rows
        best = fastest_current_pbs(rows, regions=regions)
        you = {}
        for key in keys:
            parts = key.split(":")
            if parts[0] == "star":
                row = best.get((int(parts[1]), int(parts[2]), None, "igt"))
            else:
                row = best.get((None, None, int(parts[1]), "rta"))
            if row is not None:
                you[key] = display_cs(row["frames"])
        return you

    def rank_goal_map(self, strategies: dict, targets: dict) -> dict[str, int]:
        """Each entry's rank target (`targets[key]`, a tier/division) ->
        the slowest time reaching it on that entry's own strategy ladder."""
        goal = {}
        for key, strategy in strategies.items():
            target = targets.get(key) or {}
            if target.get("tier") is None:
                continue
            ladder_cs = self.service.ranks.ladder_cs(key, strategy)
            cs = division_goal_cs(ladder_cs, target["tier"], target["division"])
            if cs is not None:
                goal[key] = cs
        return goal

    def rank_targets(self, rank: dict, strategies: dict, you: dict, ranks) -> dict:
        """The rank source's tier/division PER ENTRY. A picked division is
        the same everywhere; Automatic is `entry_automatic_goal` -- never a
        goal you already hold on that entry."""
        if ranks is None:
            return {}
        if rank.get("kind") != "automatic":
            return {key: rank for key in strategies}
        return {key: entry_automatic_goal(ranks.ladder_cs(key, strategy),
                                          you.get(key), rank)
                for key, strategy in strategies.items()}

    # `runner_times` walks EVERY target, approach, subsection and entry on the
    # sheet and answers for every runner at once, of which a runner goal keeps
    # one map. Measured 2026-09-01 on a snapshot of his own db through the real
    # endpoint (TestClient, median of 5): GET /api/scorecard costs 7.6 ms with
    # no goal, 10.8 ms against a division, 23.8 ms against one runner, 49.6 ms
    # against his stored goal (a division + three runners) and 68.9 ms against
    # four runners -- a multi goal walked the sheet once per runner SOURCE.
    # Since round 20 that fetch runs on every completed attempt, on the same
    # process as the 30 fps poller, so the walk is memoised: one per (payload,
    # adoptions, grading version), reused across sources and across fetches.
    # The memo HOLDS the payload object, so its id cannot be recycled under
    # the key while it is cached; a sheet refresh replaces the object, an
    # adoption changes the rows tuple, a version flip changes the string.
    def runner_times_for(self, version: str) -> dict[str, dict[str, int]]:
        payload = self.library.payload
        adopted_rows = self.adoptions.rows() if self.adoptions is not None else {}
        key = (id(payload), payload.get("sheet_revision"), payload.get("fetched_at"),
               version, tuple(sorted(adopted_rows.items())))
        cached = self._runner_times_memo.get("entry")
        if cached is None or cached[0] != key:
            # STRICT: a runner's time counts for a region only when it was
            # set on that ROM (round 34) -- the same stamp his import lands
            # it under, so a runner grades himself at zero whichever regions
            # are on. The Library page and the ratings keep the looser rule.
            value = runner_times(payload, adopted_rows, version=version, strict=True)
            # Keep key and value together across concurrent card reads.
            # Holding payload prevents its identity from being recycled.
            self._runner_times_memo["entry"] = (key, payload, value)
            return value
        return cached[2]

    def scorecard_regions(self, ranks) -> list[str]:
        """The regions a runner goal may offer times from, in `VALID_REGIONS`
        order and never empty. A missing or corrupt KV reads as "follow the
        detected region" -- `ranks.grading_version`, the same value
        `core/modes.py::effective_version` feeds `GET /api/mode` as
        `effective` -- rather than as a hard-coded US, so an untouched
        scorecard tracks a mode flip the way it always has."""
        stored = self.service.db.get_state(REGIONS_KEY, None)
        picked = [region for region in (stored or [])
                  if region in VALID_REGIONS] if isinstance(stored, list) else []
        if not picked:
            detected = ranks.grading_version if ranks is not None else "us"
            picked = [detected if detected in VALID_REGIONS else "us"]
        return [region for region in VALID_REGIONS if region in picked]

    def runner_goal_map(self, runner: str, regions: list[str]) -> dict[str, int]:
        """Every entity key `runner` has a sheet time for -> that time,
        already centiseconds -- straight off `library.ratings.runner_times`,
        the exact shape `_tile`'s `goal.get(key)` lookup wants (a key the
        card never asks about costs nothing to carry). Absent, never zero,
        the same rule the reader itself follows: a runner with no time on an
        entity is simply not a key here, which is what keeps `goal_coverage`
        honest about how much of the card this goal actually reaches.

        With BOTH regions on, the FASTER of the two wins per entity -- his
        round-24 words, "we simply take the faster time across both regions"
        -- which is the same minimise-then-merge shape `resolve_multi`
        already applies across goal SOURCES, one level further in. Read once
        per region (the memo below is keyed by region, so the second read is
        free after the first fetch), never by widening `ratings.py`'s own
        single-region contract: `_visible_entries` answers "what does this
        row look like on THIS ROM", and a row's JP ladder is a different
        ladder, not a longer list."""
        if self.library is None:
            return {}
        best: dict[str, int] = {}
        for region in regions:
            for key, time_cs in self.runner_times_for(region).get(runner, {}).items():
                if key not in best or time_cs < best[key]:
                    best[key] = time_cs
        return best

    def custom_goal_store(self) -> dict[str, dict[str, int]]:
        # A corrupt KV (wrong type, from a schema this store never wrote)
        # reads as absent rather than 500ing every route that touches it --
        # the same "treat garbage as no goal" rule `current_card()` applies
        # to `GOAL_KEY` below.
        store = self.service.db.get_state(CUSTOM_KEY, {})
        return store if isinstance(store, dict) else {}

    def custom_goal_map(self, keys: list[str], name: str) -> dict[str, int]:
        """A saved custom goal's own times, filtered to keys the card still
        carries -- a stale key (its segment definition since deleted) drops
        silently, same as every other "no goal on this tile" case, rather
        than surfacing a key `_tile` would never look up anyway."""
        saved = self.custom_goal_store().get(name, {})
        return {key: cs for key, cs in saved.items() if key in keys}

    def resolve_goal(self, goal_value, strategies: dict, ranks,
                     targets: dict | None = None) -> dict[str, int]:
        """One goal value -> {entity key: goal centiseconds}.

        A custom goal is typed data with no ladder lookup at all, so it
        resolves independent of `ranks` -- a broadcast-only instance can
        still grade against a hand-picked target even though it can never
        grade against a division or a runner.

        A MULTI goal resolves each source through this same function and
        keeps the FASTEST offer per entity -- his round-16 correction of
        round 14's reading ("I meant we should take the FASTEST time from
        all of the runners / all of the options provided"), verified
        against the Library: on Scale the Mountain his card showed Toad 1's
        19"02 while ikori has 16"53, because the merge had been taking the
        slowest source. Two levels, both his words: WITHIN an option, the
        fastest that option has on that star across every strategy
        (`ratings.best_entries` already minimises over every approach and
        subsection mapping to the entity -- RONC3NA's 17"50 on "Log firsty"
        beats his own 17"83 on the plain row); ACROSS options, the fastest
        of those. Coverage is still the UNION, so a runner who never
        entered a star is covered by whoever did ("if you are tracking
        multiple different runners, then you should have 100% coverage
        across all stars")."""
        if not goal_value:
            return {}
        kind = goal_value.get("kind")
        if kind == "custom":
            return self.custom_goal_map(strategies, goal_value["name"])
        if kind == "multi":
            merged, _, _ = self.resolve_multi(goal_value, strategies, ranks, targets)
            return merged
        if ranks is None:
            return {}
        if kind in ("division", "automatic"):
            return self.rank_goal_map(strategies, targets if targets is not None
                                 else {key: goal_value for key in strategies})
        if kind == "runner":
            return self.runner_goal_map(goal_value["runner"], self.scorecard_regions(ranks))
        return {}

    def resolve_multi(self, goal_value, strategies: dict, ranks, targets: dict | None = None):
        """A multi goal -> final times, source indices, and player-only times.

        WHICH source won is recorded where the comparison happens, not
        re-derived later: the card's attribution -- his round-15 ask, a
        coloured dot after each star naming the pick that set it ("that
        clearly tells us that player two is the reason that the goal is
        that time") -- has to agree with the number beside it, and the only
        way two surfaces cannot disagree is for one of them to never
        compute it. Ties keep the EARLIER source, so the legend order is
        also the tie-break and the attribution never depends on dict
        iteration order."""
        merged: dict[str, int] = {}
        owner: dict[str, int] = {}
        players: dict[str, int] = {}
        for index, source in enumerate(goal_value.get("sources") or []):
            if not isinstance(source, dict) or source.get("kind") == "multi":
                continue                     # never nest; a corrupt KV is empty
            for key, cs in self.resolve_goal(source, strategies, ranks, targets).items():
                if source.get("kind") == "runner":
                    players[key] = min(players.get(key, cs), cs)
                if key not in merged or cs < merged[key]:
                    merged[key] = cs
                    owner[key] = index
        return merged, owner, players

    def entry_pins(self) -> dict:
        store = self.service.db.get_state(ENTRY_GOALS_KEY, {})
        return store if isinstance(store, dict) else {}

    def pin_cs(self, key: str, pin: dict, strategy, ranks, regions) -> int | None:
        """A pin's one threshold on this entry; None where it offers none
        (no ladder, or the runner never ran it) -- it is still a pin."""
        kind = pin.get("kind")
        if kind == "time":
            return pin.get("cs")
        if kind == "division" and ranks is not None:
            return division_goal_cs(ranks.ladder_cs(key, strategy),
                                    pin.get("tier"), pin.get("division"))
        if kind == "runner":
            return self.runner_goal_map(pin.get("runner"), regions).get(key)
        return None

    def resolve_entries(self, rows_spec: list[dict], scope_id: str) -> dict:
        """Every goal fact for the entries in `rows_spec`, resolved once --
        the ONE derivation the card's tiles and `GET /entry_goals` both read,
        so a Practice card and its Scorecard tile cannot disagree.

        Each entry resolves independently: its strategy, its offers and its
        pin depend on its own key and label, and the scope contributes only
        its Automatic rank. That is what lets `/entry_goals` resolve just the
        keys it was asked for and still match the tile."""
        keys = card_keys(rows_spec)
        ranks = self.service.ranks
        regions = self.scorecard_regions(ranks)
        you = self.your_times(keys, regions)
        active = ActiveStrats.from_db(self.service.db, self.service.strat_by_star,
                                      self.service.strat_by_segment)
        strategies = tile_strategies(rows_spec, ranks, active,
            self.library.payload if self.library is not None else {},
            self.adoptions.rows() if self.adoptions is not None else {})
        sources = sources_of(self.service.db.get_state(GOAL_KEY, None))
        if sources[0]["kind"] == "automatic":
            rank = _score_scope(self.service, scope_id) if ranks is not None else {}
            sources[0] = automatic_goal(rank.get("tier"), rank.get("division"))
        goal_value = combined(sources)
        targets = self.rank_targets(sources[0], strategies, you, ranks)
        if len(sources) > 1:
            goal_map, goal_owner, player_goals = self.resolve_multi(
                goal_value, strategies, ranks, targets)
        else:
            goal_map, goal_owner = self.resolve_goal(goal_value, strategies, ranks, targets), {}
            player_goals = {}
        stored = self.entry_pins()
        pins = {key: stored[key] for key in keys if isinstance(stored.get(key), dict)}
        pinned = {key: self.pin_cs(key, pin, strategies.get(key), ranks, regions)
                  for key, pin in pins.items()}
        return {"you": you, "strategies": strategies, "sources": sources,
                "goal_value": goal_value, "targets": targets,
                "goal": {**goal_map, **pinned}, "owner": goal_owner,
                "players": player_goals, "pins": pins}

    def entry_spec(self, keys: list[str], card_rows: list[dict]) -> list[dict]:
        """A one-row spec holding exactly `keys`. An entry on the scope's
        card keeps the card's own label and clock, so it resolves to the
        tile's strategy; any other key -- a user segment, a 100c companion
        merged into the combined cell, an excluded entry -- resolves alone."""
        on_card = {key: (label, clock) for row in card_rows
                   for key, label, clock in row["entries"]}
        segment_names = None
        entries = []
        for key in keys:
            if key in on_card:
                entries.append((key, *on_card[key]))
                continue
            parts = key.split(":")
            if parts[0] == "star":
                label = SPECIAL_STAR_LABELS.get(key, star_name(int(parts[1]), int(parts[2])))
                entries.append((key, label, "igt"))
            else:
                if segment_names is None:
                    segment_names = {row["id"]: row["name"]
                                     for row in self.service.db.segment_defs()}
                entries.append((key, segment_names.get(int(parts[1]), ""), "rta"))
        return [{"course_id": None, "label": "", "entries": entries}]

    def entry_options(self, key: str, strategy) -> dict:
        """What a Practice card can pin on one entry, each fastest first:
        every finite division on its strategy ladder (never Capless V) and
        every runner with a time on it in the card's regions."""
        ranks = self.service.ranks
        divisions = []
        ladder_cs = ranks.ladder_cs(key, strategy) if ranks is not None else {}
        for tier in RANK_NAMES:
            for division in DIVISION_NUMERALS:
                cs = (division_goal_cs(ladder_cs, tier, division)
                      if valid_division(tier, division) else None)
                if cs is not None:
                    divisions.append({"tier": tier, "division": division, "cs": cs})
        best: dict[str, int] = {}
        for region in self.scorecard_regions(ranks) if self.library is not None else ():
            for runner, times in self.runner_times_for(region).items():
                cs = times.get(key)
                if cs is not None and cs < best.get(runner, cs + 1):
                    best[runner] = cs
        return {"divisions": sorted(divisions, key=lambda row: row["cs"]),
                "runners": [{"runner": runner, "cs": cs} for runner, cs
                            in sorted(best.items(), key=lambda item: (item[1], item[0]))]}

    def set_pin(self, key: str, pin: dict | None) -> None:
        """Store (or clear) one pin. `set_at` moves only when the threshold
        or the repeat changed, so re-saving the same goal keeps its window."""
        pins = dict(self.entry_pins())
        if pin is None:
            pins.pop(key, None)
        else:
            old = pins.get(key) if isinstance(pins.get(key), dict) else {}
            unchanged = {k: v for k, v in old.items() if k != "set_at"} == pin
            set_at = old.get("set_at") if unchanged and old.get("set_at") else (
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
            pins[key] = {**pin, "set_at": set_at}
        self.service.db.set_state(ENTRY_GOALS_KEY, pins)

    def forgiven(self) -> dict[str, list[int]]:
        store = self.service.db.get_state(FORGIVEN_KEY, {})
        return store if isinstance(store, dict) else {}

    def forgive(self, key: str, attempt_id: int, undo: bool = False) -> None:
        store = dict(self.forgiven())
        ids = [i for i in store.get(key, []) if i != attempt_id]
        if not undo:
            ids.append(attempt_id)
        if ids:
            store[key] = ids
        else:
            store.pop(key, None)
        self.service.db.set_state(FORGIVEN_KEY, store)

    def entry_progress(self, resolved: dict, clocks: dict[str, str]) -> dict:
        """{key: progress} for every repeat pin among `clocks`' keys -- ONE
        read of the current session's attempts, however many keys ask."""
        repeat = {key: pin for key, pin in resolved["pins"].items()
                  if key in clocks and pin.get("count", 1) > 1}
        if not repeat:
            return {}
        db, session_id = self.service.db, self.service.session_id
        attempts = db.session_attempts(session_id) if session_id is not None else []
        started = db.session_started_utc(session_id) if session_id is not None else None
        by_key: dict[str, list] = {}
        for attempt in attempts:
            by_key.setdefault(entry_key_of(attempt), []).append(attempt)
        forgiven = self.forgiven()
        progress = {}
        for key, pin in repeat.items():
            since = parse_utc(pin.get("set_at") or "1970-01-01T00:00:00Z")
            if started is not None:
                since = max(since, parse_utc(started))
            progress[key] = goal_progress(
                by_key.get(key, []), clock=clocks[key], goal_cs=resolved["goal"].get(key),
                count=pin["count"], streak=bool(pin.get("streak")), since=since,
                forgiven=frozenset(forgiven.get(key, ())))
        return progress
