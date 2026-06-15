"""Full-game run timer — forgiving RTA over a route (spec 2026-06-14, Phase D).

A RUN is one continuous attempt at a whole route. run_started (journaled by
start_run) ARMS run mode with the route snapshot + start_offset; the clock then
starts at 0 on the NEXT game_reset (F1). Each later game_reset ABORTS the
in-progress run (saved) and restarts a fresh one; the final step FINISHES it.

Forgiving: the wall clock never stops for a step-reset — a step's elapsed time
rolls up all its retries. Step completion = a closed SUCCESS attempt matching
the current step's candidate; a group needs K DISTINCT candidates (no dups),
any order. Times come from event wall_time (the run clock is wall-clock RTA,
NOT game frames — user decision; start_offset models the SM64 emulator
reset-timing convention). Stored offset-free; display adds the offset.

Pure over the journal: re-derives every run on replay (the runs table is a
cache like attempts). Run id = the game_reset journal id that started it.
Pause-aware subtraction is deferred (v1 = pure RTA from start)."""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RunRecord:
    id: int                  # journal id of the starting game_reset
    route_id: int | None
    route_name: str
    route_steps: list
    mode: str
    status: str              # "finished" | "aborted"
    reached_step: int
    total_ms: int | None
    start_offset_ms: int
    started_utc: str
    ended_utc: str
    is_pb: bool
    splits: list             # [{step_index, completed_item, elapsed_ms, attempts, fails}]

    def as_row(self) -> dict:
        """Dict shaped for db.insert_run / db.runs round-trips."""
        return {"id": self.id, "route_id": self.route_id,
                "route_name": self.route_name, "route_steps": self.route_steps,
                "mode": self.mode, "status": self.status,
                "reached_step": self.reached_step, "total_ms": self.total_ms,
                "start_offset_ms": self.start_offset_ms,
                "started_utc": self.started_utc, "ended_utc": self.ended_utc,
                "is_pb": self.is_pb, "splits": self.splits}


def _ms(a_utc: str, b_utc: str) -> int:
    a = datetime.fromisoformat(a_utc.replace("Z", "+00:00"))
    b = datetime.fromisoformat(b_utc.replace("Z", "+00:00"))
    return int((b - a).total_seconds() * 1000)


def _cand_matches(cand: dict, a) -> bool:
    if cand["type"] == "segment":
        return a.segment_id == cand["segment_id"]
    return (a.segment_id is None and a.course_id == cand["course"]
            and a.star_id == cand["star"])


def _cand_key(cand: dict):
    return ("seg", cand["segment_id"]) if cand["type"] == "segment" \
        else ("star", cand["course"], cand["star"])


class RunTracker:
    """One active run + accumulated finished/aborted runs. Pure over the feed;
    the projector embeds it (mirrors SegmentEngine)."""

    def __init__(self):
        self._armed = None       # {route_id, route_name, route_steps, mode, offset}
        self._active = None      # active run state, or None
        self._finished: list[RunRecord] = []   # all produced (for is_pb)
        self.run_notices: list[dict] = []       # live broadcast queue

    # -- queries -------------------------------------------------------------
    def active_run_view(self) -> dict | None:
        if self._active is None:
            return None
        act, steps = self._active, self._armed["route_steps"]
        return {"id": act["id"], "route_id": self._armed["route_id"],
                "route_name": self._armed["route_name"], "mode": self._armed["mode"],
                "started_utc": act["started_utc"],
                "start_offset_ms": self._armed["offset"],
                "current_step": act["current"],
                "steps": [{"index": i, "need": steps[i]["need"],
                           "done": list(p["done"]), "attempts": p["attempts"],
                           "fails": p["fails"], "elapsed_ms": p["elapsed_ms"]}
                          for i, p in enumerate(act["steps"])]}

    def finished_runs(self) -> list[RunRecord]:
        return list(self._finished)

    # -- feed ----------------------------------------------------------------
    def feed(self, ev, closed) -> list[RunRecord]:
        produced = []
        if ev.type == "run_started":
            if self._active is not None:
                produced.append(self._finalize("aborted", ev.wall_time_utc))
            p = ev.payload
            self._armed = {"route_id": p.get("route_id"),
                           "route_name": p.get("route_name", ""),
                           "route_steps": p.get("route_steps", []),
                           "mode": p.get("mode", "forgiving"),
                           "offset": int(p.get("start_offset_ms", 0))}
            self._active = None
        elif ev.type == "run_ended":
            if self._active is not None:
                produced.append(self._finalize("aborted", ev.wall_time_utc))
            self._armed = None
            self._active = None
        elif ev.type == "game_reset":
            if self._armed is not None:
                if self._active is not None:
                    produced.append(self._finalize("aborted", ev.wall_time_utc))
                self._begin(ev)
        if self._active is not None and closed:
            for a in closed:
                fin = self._apply(a, ev)
                if fin is not None:
                    produced.append(fin)
                    break
        for r in produced:
            self._finished.append(r)
        self._set_notices(produced)
        return produced

    # -- internals -----------------------------------------------------------
    def _begin(self, ev) -> None:
        self._active = {
            "id": ev.id, "started_utc": ev.wall_time_utc, "current": 0,
            "steps": [{"done": [], "attempts": 0, "fails": 0,
                       "elapsed_ms": None, "completed_item": None}
                      for _ in self._armed["route_steps"]]}

    def _apply(self, a, ev):
        act, steps = self._active, self._armed["route_steps"]
        i = act["current"]
        if i >= len(steps):
            return None
        step, prog = steps[i], act["steps"][i]
        matched = next((c for c in step["candidates"] if _cand_matches(c, a)), None)
        if matched is None:
            return None
        if a.outcome != "success":
            prog["attempts"] += 1
            prog["fails"] += 1
            return None
        key = _cand_key(matched)
        if key in prog["done"]:
            return None                       # no duplicate credit
        prog["done"].append(key)
        prog["attempts"] += 1
        if len(prog["done"]) >= step["need"]:
            prog["elapsed_ms"] = _ms(act["started_utc"], ev.wall_time_utc)
            prog["completed_item"] = matched
            act["current"] += 1
            if act["current"] >= len(steps):
                return self._finalize("finished", ev.wall_time_utc)
        return None

    def _finalize(self, status: str, ended_utc: str) -> RunRecord:
        act, steps = self._active, self._armed["route_steps"]
        splits = [{"step_index": i, "completed_item": p["completed_item"],
                   "elapsed_ms": p["elapsed_ms"], "attempts": p["attempts"],
                   "fails": p["fails"]}
                  for i, p in enumerate(act["steps"]) if p["elapsed_ms"] is not None]
        total = _ms(act["started_utc"], ended_utc)
        is_pb = False
        if status == "finished":
            prior = [r.total_ms for r in self._finished
                     if r.status == "finished" and r.route_id == self._armed["route_id"]
                     and r.total_ms is not None]
            is_pb = not prior or total < min(prior)
        rec = RunRecord(
            id=act["id"], route_id=self._armed["route_id"],
            route_name=self._armed["route_name"],
            route_steps=self._armed["route_steps"], mode=self._armed["mode"],
            status=status, reached_step=act["current"], total_ms=total,
            start_offset_ms=self._armed["offset"], started_utc=act["started_utc"],
            ended_utc=ended_utc, is_pb=is_pb, splits=splits)
        self._active = None
        return rec

    def _set_notices(self, produced) -> None:
        notices = []
        for r in produced:
            notices.append({"event": "run_finished" if r.status == "finished"
                            else "run_aborted", "run_id": r.id,
                            "status": r.status})
        if self._active is not None:
            notices.append({"event": "run_progress", "run_id": self._active["id"],
                            "current_step": self._active["current"]})
        self.run_notices = notices


def pb_run(runs: list) -> dict | None:
    """Finished run with the smallest total_ms (the PB), or None."""
    fin = [r for r in runs if r["status"] == "finished" and r["total_ms"] is not None]
    return min(fin, key=lambda r: r["total_ms"]) if fin else None


def _step_durations(run: dict) -> dict:
    """step_index -> this run's duration for that step (delta of cumulative
    elapsed_ms). Only steps that completed are present."""
    out, prev = {}, 0
    for s in run["splits"]:
        if s["elapsed_ms"] is None:
            continue
        out[s["step_index"]] = s["elapsed_ms"] - prev
        prev = s["elapsed_ms"]
    return out


def gold_splits(runs: list, route_steps: list) -> dict:
    """step_index -> best (min) duration across finished runs whose step
    SIGNATURE matches the current route at that index (so reordering is safe).
    Returns {"durations": {i: ms}, "sum_of_best": ms|None}."""
    def sig(steps, i):
        if i >= len(steps):
            return None
        s = steps[i]
        return (s.get("need"), tuple(sorted(map(_cand_key, s["candidates"]))))
    want = [sig(route_steps, i) for i in range(len(route_steps))]
    best: dict = {}
    for r in runs:
        if r["status"] != "finished":
            continue
        durs = _step_durations(r)
        for i, d in durs.items():
            if i < len(want) and sig(r["route_steps"], i) == want[i]:
                if i not in best or d < best[i]:
                    best[i] = d
    sob = sum(best.values()) if len(best) == len(route_steps) and route_steps else None
    return {"durations": best, "sum_of_best": sob}
