"""File-backed, user-editable rank standards (data/rank_standards.json).
Store of record is a flat JSON file (hand-editable; mirrors replay_settings).
A missing/corrupt file loses to the bundled seed, then to empty."""
import json
import logging
from pathlib import Path

from sm64_events.ranks.classify import RANK_NAMES

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
        if data is None:
            seed = self._read_valid(self.seed_path)
            if seed is not None:
                self._data = seed
                try:
                    self.save()                 # materialize seed into data dir
                except OSError:
                    _log.warning("could not write %s", self.path)
                return
            _log.warning("no usable rank standards at %s; starting empty", self.path)
            data = {"version": 1, "entities": {}}
        self._data = data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2))

    # ---- reads ----
    def to_json(self) -> dict:
        return self._data

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
        self.save()

    def reset_entity(self, ek) -> None:
        seed = self._read_valid(self.seed_path) or {"entities": {}}
        if ek in seed["entities"]:
            self._data["entities"][ek] = seed["entities"][ek]
        else:
            self._data["entities"].pop(ek, None)
        self.save()
