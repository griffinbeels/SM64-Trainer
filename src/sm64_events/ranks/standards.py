"""File-backed, user-editable rank standards (data/rank_standards.json).
Store of record is a flat JSON file (hand-editable; mirrors replay_settings).
A missing/corrupt file loses to the bundled seed, then to empty."""
import json
import logging
from pathlib import Path

from sm64_events.memory.addresses import star_name
from sm64_events.ranks.classify import RANK_NAMES, resolve_cutoff_videos

_log = logging.getLogger("sm64.ranks")


def entity_key(course_id, star_id, segment_id=None) -> str:
    if segment_id is not None:
        return f"segment:{segment_id}"
    return f"star:{course_id}:{star_id}"


# A 100-coin star's strategy name is VARIANT-QUALIFIED: "100c + Race · Open".
# Forced, not stylistic — a course's 100-coin run is timed separately per EXIT
# star, and CCM's two exit-star variants both define a strategy called
# "Standard" AND one called "Open". `ladders(ek)` is one flat {strategy:
# ladder} per entity and the whole rank subsystem reads that shape, so a bare
# strategy name cannot identify a ladder here. This separator is the ONE place
# the qualification is written; tools/scrape_ranks.py imports it rather than
# repeating the literal.
VARIANT_SEP = " · "
UNGROUPED_LABEL = "Other"


def qualify(label: str, strategy: str) -> str:
    """The stored name for `strategy` under exit-star variant `label`."""
    return f"{label}{VARIANT_SEP}{strategy}"


def _default_clock(ek: str) -> str:
    return "rta" if ek.startswith("segment:") else "igt"


def _seed_version(d: dict) -> int:
    v = d.get("version")
    return v if isinstance(v, int) else 0


def _reconcile(stored: dict, seed: dict) -> dict:
    """Bring an older stored seed up to a newer bundled one. The bundled seed
    wins for community data (strategies/times, videos, jp_strategies, clock, new
    entities/strats); user-CREATED entities/strats (absent from the seed) are
    preserved. Returns a new dict (does not mutate inputs).

    KNOWN GAP (found 2026-07-23, not yet fixed): this does not clear the
    `deleted_strats` tombstone KV (storage-side, see tracking/service.py
    purge_strategy). If a future seed ships a strategy whose name matches one
    the user previously deleted on that entity, the tombstone keeps the NEW
    seeded strat hidden from every dropdown while its column still renders,
    and no UI path clears it. Fix when it bites: drop tombstones for names the
    incoming seed defines (here) or on reset_entity."""
    out = json.loads(json.dumps(seed))                 # deep copy
    oent = out.setdefault("entities", {})
    for ek, se in stored.get("entities", {}).items():
        if ek not in oent:
            oent[ek] = json.loads(json.dumps(se))      # user-created entity
            continue
        seed_strats = oent[ek].setdefault("strategies", {})
        for strat, ladder in se.get("strategies", {}).items():
            if strat not in seed_strats:
                seed_strats[strat] = json.loads(json.dumps(ladder))  # user-created strat
        # A user-MINTED exit-star variant (one xcams has no times for) is
        # user-created data exactly as the strategies filed under it are, and
        # dropping it here would strand those strategies with no group — they
        # would still be listed, under "Other", which reads as data loss.
        for label, star in se.get("exit_variants", {}).items():
            oent[ek].setdefault("exit_variants", {}).setdefault(label, star)
        if se.get("user_videos"):                      # hand-attached per-cutoff
            oent[ek]["user_videos"] = json.loads(json.dumps(se["user_videos"]))
    return out


class RankStandards:
    def __init__(self, path, seed_path=None, sheet_path=None):
        self.path = Path(path)
        self.seed_path = Path(seed_path) if seed_path else None
        # Ladders derived from the Ultimate Sheet, kept in their OWN file and
        # merged only on READ. That is what makes "a fitted ladder never
        # overwrites a vetted one" structural rather than a rule a test has to
        # remember: nothing here can write into `self._data`, so `save()`
        # cannot spill sheet-derived data into the user's standards file.
        self.sheet_path = Path(sheet_path) if sheet_path else None
        self._data = {"version": 1, "entities": {}}
        self._sheet = {}

    # ---- load / save ----
    def _read_valid(self, p):
        if not p:
            return None
        try:
            d = json.loads(Path(p).read_text())
        except (FileNotFoundError, ValueError, OSError):
            return None
        return d if isinstance(d, dict) and isinstance(d.get("entities"), dict) else None

    def _load_sheet(self) -> None:
        sheet = self._read_valid(self.sheet_path)
        self._sheet = sheet["entities"] if sheet else {}

    def load(self) -> None:
        self._load_sheet()
        data = self._read_valid(self.path)
        seed = self._read_valid(self.seed_path)
        if data is None:
            if seed is not None:
                self._data = seed
                self._materialize()                    # write seed into the data dir
                return
            _log.warning("no usable rank standards at %s; starting empty", self.path)
            self._data = {"version": 1, "entities": {}}
            return
        # existing install: refresh community data from a NEWER bundled seed,
        # preserving user-created entities/strategies. (Without this an upgraded
        # install keeps a stale seed — no videos, old times — forever.)
        if seed is not None and _seed_version(data) < _seed_version(seed):
            self._data = _reconcile(data, seed)
            self._materialize()
            _log.info("rank standards reconciled to seed v%d", _seed_version(seed))
            return
        self._data = data

    def _materialize(self) -> None:
        try:
            self.save()
        except OSError:
            _log.warning("could not write %s", self.path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2))

    # ---- reads ----
    def to_json(self) -> dict:
        return json.loads(json.dumps(self._data))

    def _entity(self, ek) -> dict:
        return self._data["entities"].get(ek, {})

    def _stored_ladders(self, ek) -> dict:
        """The user's own dict, for the paths that MUTATE it."""
        return self._entity(ek).get("strategies", {})

    def ladders(self, ek) -> dict:
        """Every ladder for this entity, vetted merged over sheet-derived.

        A new dict each call, deliberately: a caller that mutated the result
        would be editing a merge rather than the store, so the mutating paths
        take `_stored_ladders` instead."""
        vetted = self._stored_ladders(ek)
        fitted = self._sheet.get(ek)
        if not fitted:
            return dict(vetted)
        return {**fitted, **vetted}

    def is_fitted(self, ek, strat) -> bool:
        """Whether this ladder came from the sheet rather than the community's
        vetted standards -- what a surface needs to say so."""
        return strat in self._sheet.get(ek, {}) and strat not in self._stored_ladders(ek)

    def fitted_strategies(self, ek) -> list:
        return [s for s in self._sheet.get(ek, {}) if s not in self._stored_ladders(ek)]

    def ladder_cs(self, ek, strat) -> dict:
        return {r: int(round(v * 100)) for r, v in self.ladders(ek).get(strat, {}).items()}

    def clock_for(self, ek) -> str:
        return self._entity(ek).get("clock", _default_clock(ek))

    def strategies(self, ek) -> list:
        return list(self.ladders(ek).keys())

    # ---- exit-star variants (100-coin stars only) ----
    def exit_variants(self, ek) -> dict:
        """{variant label: exit star_id} for a 100-coin star; {} for anything
        else. ONE map, deliberately — a second {strategy: exit_star} map beside
        it could disagree with the name a strategy is filed under, which is the
        divergent-duplication class. A strategy belongs to the variant whose
        `"<label>" + VARIANT_SEP` prefixes its name, so the heading it is shown
        under and the exit star it is classified by are the same fact."""
        return self._entity(ek).get("exit_variants", {})

    def variant_of(self, ek, strategy) -> tuple | None:
        """(label, exit star_id) for `strategy`, or None when it belongs to no
        variant — an ordinary entity, or a hand-edited name that matches no
        prefix. Longest prefix wins, so a label that is itself a prefix of
        another ("100c + Reds" vs "100c + Reds Alt") cannot steal it."""
        best = None
        for label, star in self.exit_variants(ek).items():
            if strategy.startswith(label + VARIANT_SEP) and (
                    best is None or len(label) > len(best[0])):
                best = (label, star)
        return best

    def exit_star_options(self, ek) -> list:
        """Every star a run on this entity could end on —
        `[{star_id, name, label}]`, `label` being the existing variant's or
        None where the community has no times for that ending. [] unless the
        entity has exit-star variants at all, which is what marks it as a
        100-coin star to a client that knows nothing else about it.

        This is what makes "define your own variant" reachable: the endings
        with no label are exactly the ones xcams does not publish, and picking
        one mints the variant."""
        variants = self.exit_variants(ek)
        if not variants:
            return []
        by_star = {star: label for label, star in variants.items()}
        course = int(ek.split(":")[1])
        return [{"star_id": star, "name": star_name(course, star),
                 "label": by_star.get(star)} for star in range(6)]

    def strategy_groups(self, ek) -> list:
        """The GROUPED view of this entity's strategies, or [] when it has no
        exit-star variants. The server resolves grouping and the browser only
        renders it, so no JS ever re-derives which variant a strategy belongs
        to and there is no second implementation to drift.

        `leaf` is the name minus its variant prefix — what a dropdown shows
        under a heading. The full `name` is still the identity, and every
        surface with no heading above it (a practice-log row, a PB tag) shows
        that instead, because a leaf alone is ambiguous there."""
        variants = self.exit_variants(ek)
        if not variants:
            return []
        groups = {label: {"label": label, "exit_star": star, "strategies": []}
                  for label, star in variants.items()}
        ungrouped = []
        for strategy in self.ladders(ek):
            found = self.variant_of(ek, strategy)
            if found is None:
                ungrouped.append({"name": strategy, "leaf": strategy})
                continue
            groups[found[0]]["strategies"].append(
                {"name": strategy, "leaf": strategy[len(found[0])
                                                   + len(VARIANT_SEP):]})
        out = [g for g in groups.values() if g["strategies"]]
        if ungrouped:
            out.append({"label": UNGROUPED_LABEL, "exit_star": None,
                        "strategies": ungrouped})
        return out

    def graded_entities(self) -> list:
        """Every entity key that has at least one ladder.

        The distinction the UI needs is "has standards but no time of mine"
        versus "has no standards at all": the first shows the ladder FLOOR
        (user, 2026-07-30 — "instead of displaying a '-' we should display the
        Capless 5 icon"), the second still shows nothing, because there is no
        ladder for a floor to be the bottom of. `_strat_rank` collapses both to
        None, so the view cannot tell them apart without this.

        A ladder with no thresholds in it does not count -- an entity present
        in the file with an empty strategies dict has standards in name only.
        """
        keys = {ek for ek, entity in self._data.get("entities", {}).items()
                if any(entity.get("strategies", {}).values())}
        keys |= {ek for ek, strategies in self._sheet.items() if any(strategies.values())}
        return sorted(keys)

    def videos(self, ek) -> dict:
        return self._entity(ek).get("videos", {})

    def video_for(self, ek, strat) -> str | None:
        return self.videos(ek).get(strat)

    def clips(self, ek) -> dict:
        return self._entity(ek).get("clips", {})

    def user_videos(self, ek) -> dict:
        return self._entity(ek).get("user_videos", {})

    def seeded_strategies(self, ek) -> list:
        """Strategy names the bundled community seed defines for this entity —
        THE custom-vs-default distinction (the same one _reconcile uses).
        Seeded strats are community data: protected from full deletion."""
        seed = self._read_valid(self.seed_path)
        if seed is None:
            return []
        return list(seed["entities"].get(ek, {}).get("strategies", {}).keys())

    def cutoff_videos(self, ek) -> dict:
        """{strat: {rank: url}} — auto band videos (from clips) merged with the
        user's hand-attached overrides, resolved against each strat's ladder. THE
        per-cutoff video map the standards table links each time cell to."""
        clips, overrides = self.clips(ek), self.user_videos(ek)
        out = {}
        for strat in self.ladders(ek):
            resolved = resolve_cutoff_videos(
                self.ladder_cs(ek, strat), clips.get(strat, []), overrides.get(strat))
            if resolved:
                out[strat] = resolved
        return out

    # ---- writes ----
    def _ensure(self, ek) -> dict:
        return self._data["entities"].setdefault(
            ek, {"clock": _default_clock(ek), "strategies": {}})

    def set_threshold(self, ek, strat, rank, seconds) -> None:
        if rank not in RANK_NAMES or rank == "Iron":
            raise ValueError(f"unknown rank {rank!r}")
        self._ensure(ek)["strategies"].setdefault(strat, {})[rank] = float(seconds)
        self.save()

    def create_strategy(self, ek, strat, exit_star=None) -> str:
        """Create `strat` and return the name it was actually STORED under —
        which differs from what the caller passed whenever `exit_star` is
        given, since a 100-coin strategy is variant-qualified. The caller must
        use the returned name for every follow-up write (thresholds, videos):
        composing it a second time at the call site is exactly the second door
        `VARIANT_SEP` exists to prevent.

        An `exit_star` with no variant yet MINTS one, named for the star it
        ends on — that is the "define your own variant for an exit star the
        community has no time for" path, and it needs no separate endpoint."""
        if not strat:
            raise ValueError("strategy name required")
        if exit_star is not None:
            strat = qualify(self._variant_label(ek, int(exit_star)), strat)
        self._ensure(ek)["strategies"].setdefault(strat, {})
        self.save()
        return strat

    def _variant_label(self, ek, exit_star) -> str:
        """This entity's label for `exit_star`, minting and STORING one when
        the exit star has none. Named from our own star registry, the same
        source every other surface names a star from."""
        for label, star in self.exit_variants(ek).items():
            if star == exit_star:
                return label
        parts = ek.split(":")
        if parts[0] != "star" or len(parts) != 3:
            raise ValueError(f"{ek} has no exit stars")
        label = f"100c + {star_name(int(parts[1]), exit_star)}"
        self._ensure(ek).setdefault("exit_variants", {})[label] = exit_star
        return label

    def delete_strategy(self, ek, strat) -> None:
        # The STORED dict, not the merged read -- popping from a merge would
        # silently no-op and the strategy would still be there next load.
        self._stored_ladders(ek).pop(strat, None)
        self.user_videos(ek).pop(strat, None)
        self.save()

    def set_video(self, ek, strat, rank, url) -> None:
        """Hand-attach an example video to one (strat, rank) cutoff cell. Stored
        under the entity's user_videos so it survives a seed bump (_reconcile)."""
        if rank not in RANK_NAMES or rank == "Iron":
            raise ValueError(f"unknown rank {rank!r}")
        if not url:
            raise ValueError("video url required")
        self._ensure(ek).setdefault("user_videos", {}).setdefault(strat, {})[rank] = str(url)
        self.save()

    def clear_video(self, ek, strat, rank) -> None:
        ent = self._data["entities"].get(ek)
        if ent is None:
            return
        uv = ent.get("user_videos", {})
        if strat in uv:
            uv[strat].pop(rank, None)
            if not uv[strat]:
                uv.pop(strat)
        if not uv:
            ent.pop("user_videos", None)
        self.save()

    def reset_entity(self, ek) -> None:
        seed = self._read_valid(self.seed_path) or {"entities": {}}
        if ek in seed["entities"]:
            self._data["entities"][ek] = seed["entities"][ek]
        else:
            self._data["entities"].pop(ek, None)
        self.save()
