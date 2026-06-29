"""File-backed, user-editable rank standards (data/rank_standards.json).
Store of record is a flat JSON file (hand-editable; mirrors replay_settings).
A missing/corrupt file loses to the bundled seed, then to empty."""
import json
import logging
from pathlib import Path

from sm64_events.ranks.classify import RANK_NAMES, resolve_cutoff_videos

# the registry's color half (order lives in classify.RANK_NAMES; UI mirrors both)
RANK_COLORS = {
    "Mario": "#e23b3b", "Grandmaster": "#8b1a1a", "Master": "#7b3f9e",
    "Diamond": "#3f86d6", "Platinum": "#5cb85c", "Gold": "#e0b520",
    "Silver": "#c2c2c2", "Bronze": "#c0894a", "Iron": "#8a8a8a"}

_log = logging.getLogger("sm64.ranks")


def entity_key(course_id, star_id, segment_id=None) -> str:
    if segment_id is not None:
        return f"segment:{segment_id}"
    return f"star:{course_id}:{star_id}"


def _default_clock(ek: str) -> str:
    return "rta" if ek.startswith("segment:") else "igt"


def _seed_version(d: dict) -> int:
    v = d.get("version")
    return v if isinstance(v, int) else 0


def _reconcile(stored: dict, seed: dict) -> dict:
    """Bring an older stored seed up to a newer bundled one. The bundled seed
    wins for community data (strategies/times, videos, jp_strategies, clock, new
    entities/strats); user-CREATED entities/strats (absent from the seed) are
    preserved. Returns a new dict (does not mutate inputs)."""
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
        if se.get("user_videos"):                      # hand-attached per-cutoff
            oent[ek]["user_videos"] = json.loads(json.dumps(se["user_videos"]))
    return out


class RankStandards:
    def __init__(self, path, seed_path=None):
        self.path = Path(path)
        self.seed_path = Path(seed_path) if seed_path else None
        self._data = {"version": 1, "entities": {}}

    # ---- load / save ----
    def _read_valid(self, p):
        if not p:
            return None
        try:
            d = json.loads(Path(p).read_text())
        except (FileNotFoundError, ValueError, OSError):
            return None
        return d if isinstance(d, dict) and isinstance(d.get("entities"), dict) else None

    def load(self) -> None:
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

    def ladders(self, ek) -> dict:
        return self._entity(ek).get("strategies", {})

    def ladder_cs(self, ek, strat) -> dict:
        return {r: int(round(v * 100)) for r, v in self.ladders(ek).get(strat, {}).items()}

    def clock_for(self, ek) -> str:
        return self._entity(ek).get("clock", _default_clock(ek))

    def strategies(self, ek) -> list:
        return list(self.ladders(ek).keys())

    def videos(self, ek) -> dict:
        return self._entity(ek).get("videos", {})

    def video_for(self, ek, strat) -> str | None:
        return self.videos(ek).get(strat)

    def clips(self, ek) -> dict:
        return self._entity(ek).get("clips", {})

    def user_videos(self, ek) -> dict:
        return self._entity(ek).get("user_videos", {})

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

    def create_strategy(self, ek, strat) -> None:
        if not strat:
            raise ValueError("strategy name required")
        self._ensure(ek)["strategies"].setdefault(strat, {})
        self.save()

    def delete_strategy(self, ek, strat) -> None:
        self.ladders(ek).pop(strat, None)
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
