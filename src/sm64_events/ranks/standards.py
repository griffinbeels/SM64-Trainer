"""File-backed, user-editable rank standards (data/rank_standards.json).
Store of record is a flat JSON file (hand-editable; mirrors replay_settings).
A missing/corrupt file loses to the bundled seed, then to empty."""
import json
import logging
import math
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from copy import deepcopy
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


def _preserve_legacy_edits(stored: dict, seed: dict) -> bool:
    """Remember detectable manual edits before a future bundled seed changes.

    Only a matching seed version supplies a trustworthy baseline. An older
    unmarked file cannot distinguish a user edit from a changed seed cutoff,
    so that case retains the existing reconciliation behavior.
    """
    if _seed_version(stored) != _seed_version(seed):
        return False
    changed = False
    for ek, entity in stored.get("entities", {}).items():
        baseline = seed.get("entities", {}).get(ek, {})
        for layer, override in (("strategies", "sheet_overrides"),
                                ("jp_strategies", "sheet_jp_overrides")):
            for strat, ladder in entity.get(layer, {}).items():
                if strat not in baseline.get("strategies", {}):
                    continue
                seeded = baseline.get(layer, {}).get(strat, {})
                existing = entity.get(override, {}).get(strat, {})
                edits = {rank: value for rank, value in ladder.items()
                         if (rank not in seeded or value != seeded[rank])
                         and rank not in existing}
                if edits:
                    entity.setdefault(override, {}).setdefault(strat, {}).update(edits)
                    changed = True
    return changed


def _reconcile(stored: dict, seed: dict) -> dict:
    """Bring an older stored seed up to a newer bundled one. The bundled seed
    wins for community data (strategies/times, videos, jp_strategies, clock, new
    entities/strats); user-CREATED entities/strats (absent from the seed) are
    preserved -- their base ladder AND their JP overlay (`jp_strategies` is
    also where the standards editor writes a typed JP time, 2026-08-15). A JP
    time without explicit edit provenance on a SEEDED strategy loses to the
    seed, as an unmarked US time does. Explicit cutoff edits survive in their
    own overlays, even when the typed number equals a seed value. Returns a
    new dict (does not mutate inputs).

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
                user_jp = se.get("jp_strategies", {}).get(strat)
                if user_jp:
                    oent[ek].setdefault("jp_strategies", {})[strat] = \
                        json.loads(json.dumps(user_jp))
        # A user-MINTED exit-star variant (one xcams has no times for) is
        # user-created data exactly as the strategies filed under it are, and
        # dropping it here would strand those strategies with no group — they
        # would still be listed, under "Other", which reads as data loss.
        for label, star in se.get("exit_variants", {}).items():
            oent[ek].setdefault("exit_variants", {}).setdefault(label, star)
        if se.get("user_videos"):                      # hand-attached per-cutoff
            oent[ek]["user_videos"] = json.loads(json.dumps(se["user_videos"]))
        for layer in ("sheet_overrides", "sheet_jp_overrides", "overall_overrides", "overall_foundation"):
            if se.get(layer):
                oent[ek][layer] = json.loads(json.dumps(se[layer]))
    return out


# A strategy an OLDER seed filed under the WRONG entity, and where the seed
# files it now: (old entity, strategy) -> (new entity, the exact ladder the old
# seed published there). `_reconcile` cannot tell "the seed moved this" from
# "the user created this" -- both are simply absent from the seed's entity --
# so the v6 reconcile (the 2026-08-31 Princess's Secret Slide split) preserved
# the stale "Under 21" under the plain Slide Star as user data, and a file
# already AT v6 never reconciles again, so no version bump can reach it.
# Measured 2026-09-01 on all three live files (this worktree v6, main v5, the
# installed exe v5): each held the stale copy, byte-equal to the ladder pinned
# here. The pinned value guards unmarked data; explicit cutoff edits also
# keep the strategy, even when they equal that value. Pinned rather than
# compared against the seed's CURRENT new-home ladder because a user who skips
# from v5 straight to a seed where xcams has moved the Under-21 cutoffs would keep the stale copy
# forever. `tests/test_ranks_standards.py` checks every row against the
# bundled seed, so a row cannot outlive the move it describes.
SEED_MOVES = {
    ("star:19:0", "Under 21"): ("star:19:1", {
        "Mario": 20.73, "Grandmaster": 20.8, "Master": 20.9, "Diamond": 21.0,
        "Platinum": 21.13, "Gold": 21.4, "Silver": 21.6, "Bronze": 22.26}),
}


def _repair_moved_strats(data: dict) -> bool:
    """Drop each SEED_MOVES strategy from its old entity while its ladder is
    still the pinned stale one with no explicit cutoff edits, taking the
    community data filed under that name (JP overlay, videos, clips) with it.
    Carries a hand-attached video to the new home. Runs on EVERY load, after
    any reconcile, because
    the stale copy survives an equal-version load untouched. Mutates `data`;
    True when anything changed, so the caller writes the file back once."""
    changed = False
    entities = data.get("entities", {})
    for (old_ek, strat), (new_ek, stale_ladder) in SEED_MOVES.items():
        old = entities.get(old_ek) or {}
        if old.get("strategies", {}).get(strat) != stale_ladder:
            continue
        # An explicit edit can equal the old seed value. That provenance is
        # stronger than numeric equality, so the old name is now user data.
        if (old.get("sheet_overrides", {}).get(strat)
                or old.get("sheet_jp_overrides", {}).get(strat)):
            continue
        del old["strategies"][strat]
        for community in ("jp_strategies", "videos", "clips"):
            old.get(community, {}).pop(strat, None)
        carried = old.get("user_videos", {}).pop(strat, None)
        if carried:
            home = entities.setdefault(new_ek, {}).setdefault("user_videos", {})
            home.setdefault(strat, carried)
        if "user_videos" in old and not old["user_videos"]:
            del old["user_videos"]
        _log.info("rank standards: dropped %r from %s -- the seed files it under %s",
                  strat, old_ek, new_ek)
        changed = True
    return changed


class RankStandards:
    def __init__(self, path, seed_path=None, sheet_path=None):
        self.path = Path(path)
        self.seed_path = Path(seed_path) if seed_path else None
        # Ladders derived from the Ultimate Sheet, kept in their OWN file and
        # merged only on READ. A current fit supplies the foundation; inherited
        # seed numbers yield to it while genuine user edits overlay individual
        # cutoffs. Nothing copies a fit into the user's saved standards.
        self.sheet_path = Path(sheet_path) if sheet_path else None
        self._data = {"version": 1, "entities": {}}
        self._seed = {"entities": {}}
        self._sheet = {}
        self._sheet_jp = {}
        self._sheet_estimates = {}
        self.calibrations = None
        self._read_user = ContextVar(f"rank_user_{id(self)}", default=None)
        self._overall_cache = {}
        self._user_revision = "initial"
        self._legacy_overall = {}
        # The GRADING VERSION: what every ladder read that names no version
        # resolves on. "us" or "jp". main.py sets it from the game version
        # setting at boot and tracking/service.py::set_game_version on every
        # change, so grading follows the setting live and no caller has to
        # thread a version through -- an explicit `version=` (the visual
        # switches) always overrides it. A property so a junk value raises
        # at the write, never reads silently as US.
        self._grading_version = "us"

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
        self._sheet, self._sheet_jp = {}, {}
        for ek, entity in (sheet["entities"] if sheet else {}).items():
            if "strategies" in entity:     # v2: the vetted seed's own two-layer shape
                self._sheet[ek] = entity.get("strategies", {})
                self._sheet_jp[ek] = entity.get("jp_strategies", {})
            else:                          # v1: flat strat -> ladder
                self._sheet[ek] = entity

    def load(self) -> None:
        self._overall_cache.clear()
        self._load_sheet()
        data = self._read_valid(self.path)
        seed = self._read_valid(self.seed_path)
        self._seed = seed or {"entities": {}}
        if data is None:
            if seed is not None:
                self._data = json.loads(json.dumps(seed))
                self._remember_overall()
                self._materialize()                    # write seed into the data dir
                return
            _log.warning("no usable rank standards at %s; starting empty", self.path)
            self._data = {"version": 1, "entities": {}}
            self._remember_overall()
            self._touch()
            return
        # existing install: refresh community data from a NEWER bundled seed,
        # preserving user-created entities/strategies. (Without this an upgraded
        # install keeps a stale seed — no videos, old times — forever.)
        reconciled = seed is not None and _seed_version(data) < _seed_version(seed)
        if reconciled:
            data = _reconcile(data, seed)
            _log.info("rank standards reconciled to seed v%d", _seed_version(seed))
        # ...and then, whatever the version says, drop what an older seed filed
        # under the wrong entity (SEED_MOVES) -- the reconcile above is what
        # preserves it, and an already-current file never gets here otherwise.
        repaired = _repair_moved_strats(data)
        preserved = seed is not None and _preserve_legacy_edits(data, seed)
        self._data = data
        self._remember_overall()
        self._touch()
        if reconciled or repaired or preserved:
            self._materialize()

    def _materialize(self) -> None:
        try:
            self.save()
        except OSError:
            _log.warning("could not write %s", self.path)

    def save(self) -> None:
        # A strategy edit must not quietly change a legacy Overall fallback,
        # including after restart. Save its original basis only where needed.
        for key, entity in self._data["entities"].items():
            foundation = self._legacy_overall.get(key)
            if foundation and self._foundation_of(entity) != foundation:
                entity.setdefault("overall_foundation", deepcopy(foundation))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2))
        self._touch()

    @staticmethod
    def _foundation_of(entity):
        return {key: deepcopy(entity.get(key, {})) for key in ("strategies", "jp_strategies")}

    def _remember_overall(self):
        self._legacy_overall = {
            key: deepcopy(entity.get("overall_foundation") or self._foundation_of(entity))
            for key, entity in self._data["entities"].items()}

    def _overall_fallback(self, ek, version):
        pinned = self._read_user.get()
        foundations = pinned[3] if pinned else self._legacy_overall
        foundation = foundations.get(ek, self._seed["entities"].get(ek, {}))
        base = {**foundation.get("strategies", {}), **self._fitted(ek)}
        if version == "jp":
            jp = {**foundation.get("jp_strategies", {}), **self._fitted(ek, "jp_strategies")}
            base = {name: {**ladder, **jp.get(name, {})} for name, ladder in base.items()}
        return base

    def _touch(self):
        from sm64_events.ranks.calibration import fingerprint
        self._user_revision = fingerprint(self._data)
        self._overall_cache.clear()

    @contextmanager
    def read_context(self):
        """Pin generated ranks, user overrides, and the grading ROM for one read."""
        pinned = self._read_user.get()
        state = pinned or (deepcopy(self._data), self._user_revision, self._grading_version,
                           deepcopy(self._legacy_overall))
        token = self._read_user.set(state)
        context = self.calibrations.pin() if self.calibrations else nullcontext()
        try:
            with context:
                yield
        finally:
            self._read_user.reset(token)

    def _read_data(self):
        pinned = self._read_user.get()
        return pinned[0] if pinned else self._data

    def _fitted(self, ek, layer="strategies"):
        generation = self.calibrations.read if self.calibrations else None
        if generation is not None:
            return generation.layers.get(ek, {}).get(layer, {})
        source = {"strategies": self._sheet, "jp_strategies": self._sheet_jp,
                  "estimates": self._sheet_estimates}
        return source[layer].get(ek, {})

    @property
    def scoring_rows(self):
        generation = self.calibrations.read if self.calibrations else None
        return dict(generation.scoring_rows) if generation is not None else None

    @property
    def calibration_revision(self):
        from sm64_events.ranks.calibration import fingerprint
        generation = self.calibrations.read if self.calibrations else None
        pinned = self._read_user.get()
        user = pinned[1] if pinned else self._user_revision
        return fingerprint([generation.revision if generation else
                            [self._sheet, self._sheet_jp, self._sheet_estimates],
                            user, self.grading_version])

    def overall_curve(self, ek, version=None):
        """Resolve Overall independently from editable strategy cutoffs."""
        from sm64_events.ranks import curves, scoring
        version = self._resolve(version)
        cache_key = (self.calibration_revision, ek, version)
        if self._overall_cache and next(iter(self._overall_cache))[0] != cache_key[0]:
            self._overall_cache.clear()
        if cache_key not in self._overall_cache:
            generation = self.calibrations.read if self.calibrations else None
            generated = generation.overall.get(ek, {}).get(version) if generation else None
            if generation is not None and ek in generation.overall and generated is None:
                generated = curves.from_ladder({}, metadata={
                    "source": "missing", "version": version,
                    "note": "No compatible population for this game version."})
            curve = generated or curves.from_ladder(
                scoring.best_ladder(self._overall_fallback(ek, version) if self.calibrations is not None
                                    else self.ladders(ek, version)),
                metadata={"source": "legacy", "estimated": True,
                          "note": "No compatible population; using existing standards."})
            pins = self.overall_overrides(ek, version)
            if pins:
                curve = curves.with_anchors(curve, {rank: int(round(value * 100))
                                                    for rank, value in pins.items()},
                                             preserve_unpinned=False)
            self._overall_cache[cache_key] = curve
        return deepcopy(self._overall_cache[cache_key])

    def overall_overrides(self, ek, version=None):
        return dict(self._entity(ek).get("overall_overrides", {}).get(self._resolve(version), {}))

    def set_overall_threshold(self, ek, rank, seconds, version="us"):
        from sm64_events.ranks import curves
        if rank not in RANK_NAMES or rank == "Iron":
            raise ValueError(f"unknown rank {rank!r}")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("an Overall cutoff must be a positive finite time")
        version = self._resolve(version)
        curves.with_anchors(self.overall_curve(ek, version), {rank: int(round(seconds * 100))})
        self._ensure(ek).setdefault("overall_overrides", {}).setdefault(version, {})[rank] = float(seconds)
        self.save()

    def reset_overall(self, ek, version=None):
        overrides = self._ensure(ek).get("overall_overrides", {})
        if version is None:
            self._ensure(ek).pop("overall_overrides", None)
        else:
            overrides.pop(self._resolve(version), None)
        self.save()

    # ---- reads ----
    def to_json(self) -> dict:
        return deepcopy(self._read_data())

    def _entity(self, ek) -> dict:
        return self._read_data()["entities"].get(ek, {})

    def _stored_ladders(self, ek) -> dict:
        """The user's own dict, for the paths that MUTATE it."""
        return self._data["entities"].get(ek, {}).get("strategies", {})

    def _stored_cutoffs(self, ek, strat, layer) -> dict:
        """Stored values that may overlay the current foundation.

        A materialized seed is not an edit. With a Sheet foundation, only
        differences from the seed are legacy manual changes; explicit edits
        have their own overlays so typing the old seed value still counts.
        Without a fit, the whole stored ladder remains authoritative.
        """
        stored = self._entity(ek).get(layer, {}).get(strat, {})
        if not self.is_fitted(ek, strat):
            return stored
        baseline = self._seed["entities"].get(ek, {}).get(layer, {}).get(strat, {})
        return {rank: value for rank, value in stored.items()
                if rank not in baseline or value != baseline[rank]}

    def ladders(self, ek, version=None) -> dict:
        """Every ladder for this entity, user edits over the current Sheet fit,
        RESOLVED on `version` -- "us", "jp", or None for the grading version.
        On "jp" each strategy's annotated JP values overlay its base ladder
        rank by rank (`jp_deltas`); a strategy with no annotation is the same
        ladder in both versions (user's rule, 2026-08-07: combined unless a
        difference is written down).

        A new dict each call, deliberately: a caller that mutated the result
        would be editing a merge rather than the store, so the mutating paths
        take `_stored_ladders` instead."""
        stored = self._entity(ek).get("strategies", {})
        fitted = self._fitted(ek)
        base = {strat: {**fitted.get(strat, {}),
                        **self._stored_cutoffs(ek, strat, "strategies")}
                for strat in dict.fromkeys([*fitted, *stored])}
        for strat, overrides in self._entity(ek).get("sheet_overrides", {}).items():
            base[strat] = {**base.get(strat, {}), **overrides}
        if self._resolve(version) != "jp":
            return base
        return {strat: {**ladder, **self.jp_deltas(ek, strat)}
                for strat, ladder in base.items()}

    @property
    def grading_version(self) -> str:
        pinned = self._read_user.get()
        return pinned[2] if pinned else self._grading_version

    @grading_version.setter
    def grading_version(self, version: str) -> None:
        if version not in ("us", "jp"):
            raise ValueError(f"unknown game version {version!r}")
        self._grading_version = version

    def _resolve(self, version) -> str:
        if version is None:
            return self.grading_version
        if version not in ("us", "jp"):
            raise ValueError(f"unknown game version {version!r}")
        return version

    def apply_sheet_ladders(self, mapping: dict) -> None:
        """Merge user-assigned library ladders into the sheet-derived layer.

        Same layer as the bundled ones on purpose: both are sheet-derived,
        both supply the foundation beneath user edits, and neither is copied
        into the user's standards file. Called whenever an assignment changes, so
        it REPLACES what it added last time rather than accumulating."""
        layers = self.sheet_layers(mapping)
        self._sheet = {ek: layer.get("strategies", {}) for ek, layer in layers.items()}
        self._sheet_jp = {ek: layer.get("jp_strategies", {}) for ek, layer in layers.items()}
        self._sheet_estimates = {ek: layer.get("estimates", {}) for ek, layer in layers.items()}
        self._overall_cache.clear()

    def sheet_layers(self, mapping):
        """Prepare complete generated layers without changing any reader's revision."""
        sheet = self._read_valid(self.sheet_path)
        out = {}
        for ek, entity in (sheet["entities"] if sheet else {}).items():
            out[ek] = deepcopy(entity if "strategies" in entity else {"strategies": entity})
        for ek, entity in (mapping or {}).items():
            layers = entity if "strategies" in entity else {"strategies": entity}
            for kind in ("strategies", "jp_strategies", "estimates"):
                out.setdefault(ek, {}).setdefault(kind, {}).update(deepcopy(layers.get(kind, {})))
        return out

    def estimated_strategies(self, ek) -> dict:
        """Provisional Sheet cutoffs, with the evidence that supplied them."""
        return {name: estimate for name, estimate
                in self._fitted(ek, "estimates").items()
                if self.is_fitted(ek, name)}

    def is_fitted(self, ek, strat) -> bool:
        """Whether this strategy has a Sheet foundation, including seeded names."""
        return strat in self._fitted(ek)

    def fitted_strategies(self, ek) -> list:
        return list(self._fitted(ek))

    def jp_deltas(self, ek, strat) -> dict:
        """{rank: JP seconds} where the JP time is ANNOTATED as different.
        Sources merge RANK BY RANK: the Sheet's fitted JP ladder underneath,
        legacy manual changes and explicitly typed JP times on top. Unchanged
        seed annotations yield to a Sheet foundation just like US cutoffs.
        Per rank rather than whole-ladder, so one
        edited JP rank on a sheet-fitted strategy keeps the other fitted JP
        ranks instead of hiding them. Empty means no annotated difference,
        and the base ladder applies to BOTH versions (user's rule,
        2026-08-07: combined unless a difference is written down)."""
        return {**self._fitted(ek, "jp_strategies").get(strat, {}),
                **self._stored_cutoffs(ek, strat, "jp_strategies"),
                **self._entity(ek).get("sheet_jp_overrides", {}).get(strat, {})}

    def has_jp_ladder(self, ek, strat) -> bool:
        return bool(self.jp_deltas(ek, strat))

    def jp_strategies(self, ek) -> list:
        """The strategies whose JP ladder differs from their US one -- what
        the standards editor opens a JP column for."""
        return [strat for strat in self.ladders(ek, "us") if self.jp_deltas(ek, strat)]

    def clearable_jp_strategies(self, ek) -> list:
        """Strategies with an effective JP overlay that `clear_jp` can drop.

        Inherited seed annotations count only without a Sheet foundation;
        explicit edits and legacy manual changes count in either case. The
        Sheet's JP ladder is read-only and never makes a strategy clearable.
        """
        explicit = self._entity(ek).get("sheet_jp_overrides", {})
        return [strat for strat in self.ladders(ek, "us")
                if explicit.get(strat) or self._stored_cutoffs(ek, strat, "jp_strategies")]

    def ladder_cs(self, ek, strat, version=None) -> dict:
        """The ladder a time on `version` grades against, in centiseconds --
        `ladders(ek, version)[strat]`, so the two can never disagree. None
        means the grading version (`self.grading_version`), which is how every
        rank surface follows the game version setting without naming it."""
        return {r: int(round(v * 100))
                for r, v in self.ladders(ek, version).get(strat, {}).items()}

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
        keys = {ek for ek, entity in self._read_data().get("entities", {}).items()
                if any(entity.get("strategies", {}).values())
                or entity.get("overall_overrides") or entity.get("overall_foundation")}
        generation = self.calibrations.read if self.calibrations else None
        fitted_keys = generation.layers if generation else self._sheet
        keys |= {ek for ek in fitted_keys if any(self._fitted(ek).values())}
        if generation:
            keys.update(generation.overall)
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
        return list(self._seed["entities"].get(ek, {}).get("strategies", {}))

    def cutoff_videos(self, ek, extra_clips=None, dead_urls=None, version=None) -> dict:
        """{strat: {rank: url}} — auto band videos (from clips) merged with the
        user's hand-attached overrides, resolved against each strat's ladder. THE
        per-cutoff video map the standards table links each time cell to.

        `extra_clips` ({strat: [[cs, url], ...]}) widens the clip pool per
        strategy — the library entries `library/examples.py` resolves (task
        0098). Merged INTO the band resolution rather than layered over it, so
        the fastest example within a tier's band wins whichever source it came
        from; user overrides still outrank both.

        `dead_urls` (round 2) drops clips whose video no longer resolves
        BEFORE the band resolution, which is what makes "choose the next
        eligible video" fall out of the existing fastest-in-band rule instead
        of needing a second pass. User overrides are deliberately NOT
        filtered: a hand-attached URL is his fact, not the community's."""
        clips, overrides = self.clips(ek), self.user_videos(ek)
        extra = extra_clips or {}
        dead = dead_urls or set()
        out = {}
        for strat in self.ladders(ek):
            merged = [clip for clip
                      in list(clips.get(strat, [])) + list(extra.get(strat, []))
                      if clip[1] not in dead]
            resolved = resolve_cutoff_videos(
                self.ladder_cs(ek, strat, version), merged, overrides.get(strat))
            if resolved:
                out[strat] = resolved
        return out

    # ---- writes ----
    def _ensure(self, ek) -> dict:
        return self._data["entities"].setdefault(
            ek, {"clock": _default_clock(ek), "strategies": {}})

    def set_threshold(self, ek, strat, rank, seconds, version="us") -> None:
        """`version="jp"` writes the strategy's JP OVERLAY (the same
        `jp_strategies` shape the vetted seed ships), never its base ladder;
        "us" writes the base ladder, which is what both versions grade on
        wherever no JP time is annotated."""
        if rank not in RANK_NAMES or rank == "Iron":
            raise ValueError(f"unknown rank {rank!r}")
        layer = "jp_strategies" if self._resolve(version) == "jp" else "strategies"
        # Record intent even without a current fit: a later Sheet assignment
        # must preserve an edit that happens to equal the old seed number.
        override = "sheet_jp_overrides" if layer == "jp_strategies" else "sheet_overrides"
        entity = self._ensure(ek)
        if layer == "jp_strategies" or not self.is_fitted(ek, strat):
            entity.setdefault(layer, {}).setdefault(strat, {})[rank] = float(seconds)
        entity.setdefault(override, {}).setdefault(strat, {})[rank] = float(seconds)
        self.save()

    def clear_jp(self, ek, strat) -> None:
        """Drop the user's JP overlay for `strat` -- the editor's "JP timed
        differently" toggle turned off. The base ladder then applies to both
        versions again. A sheet-fitted JP ladder is not the user's to clear
        and stays (it lives in its own read-only layer)."""
        self._entity(ek).get("jp_strategies", {}).pop(strat, None)
        self._entity(ek).get("sheet_jp_overrides", {}).pop(strat, None)
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
        self._entity(ek).get("sheet_overrides", {}).pop(strat, None)
        self._entity(ek).get("sheet_jp_overrides", {}).pop(strat, None)
        self.user_videos(ek).pop(strat, None)
        # Its JP overlay goes with it -- found 2026-08-15 by the modal's own
        # test cleanup: without this a deleted JP-carrying strategy left an
        # orphan in jp_strategies forever, and re-creating the name would
        # have inherited JP times its author never typed.
        self._entity(ek).get("jp_strategies", {}).pop(strat, None)
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
        original = self._entity(ek)
        overall = {key: deepcopy(original[key]) for key in ("overall_overrides", "overall_foundation")
                   if key in original}
        if ek in self._legacy_overall:
            overall.setdefault("overall_foundation", deepcopy(self._legacy_overall[ek]))
        if ek in self._seed["entities"]:
            self._data["entities"][ek] = json.loads(json.dumps(self._seed["entities"][ek]))
        else:
            self._data["entities"].pop(ek, None)
        if overall:
            self._ensure(ek).update(overall)
        self.save()
