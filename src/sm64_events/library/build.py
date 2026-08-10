"""Sheet rows -> the library payload, plus the coverage report.

Two version fields answer different questions and both ship. `schema_version`
is ours: an older one forces a rebuild. `sheet_revision` is the Ultimate
Sheet's own newest Log entry, and it is what makes "the newest snapshot wins"
answerable from the SOURCE rather than from whenever we happened to fetch --
so an in-app refresh is never undone by a later app update carrying an older
snapshot, in either direction.

Several sheet targets legitimately share one entity key (every "+ 100c" row is
the course's 100-coin star). They stay separate targets here: they are
genuinely different runs, and collapsing them would merge four CCM routes into
one unreadable list."""
import re

from sm64_events.library.mapping import map_target, miss_reason
from sm64_events.library.sheet import read_rows
from sm64_events.library.workbook import log_revision

# 2: approaches carry `matched_strategy` (2026-08-07). The Library page
# REQUIRES it, so a pre-stamp local snapshot (a dev refresh) must lose to the
# stamped bundled one -- newest-sheet-revision-wins would otherwise keep a
# stale local copy whose shape the page cannot read.
SCHEMA_VERSION = 2

_VERSION_SUFFIX = re.compile(r"\s*\((?:JP|US)\)\s*$", re.I)


def _base_name(label: str) -> str:
    """The label minus its ROM-version suffix -- what pairs a (JP) row with
    its (US) sibling. Only that suffix is stripped: every other parenthetical
    ("PAUSE TIME INCLUDED", "120 star file") distinguishes real rows."""
    return _VERSION_SUFFIX.sub("", label).strip()


def _entries(row) -> list:
    # The ROM version rides on every entry, not just on the approach. A (JP)
    # row and its (US) sibling merge into one approach but hold two genuinely
    # different populations -- JRB's stone pillar is 10.80 JP against 14.50 US
    # -- so anything fitting a ladder over the merged pile describes neither.
    return [{"runner": runner, "time_cs": centiseconds, "video": link,
             "version": row.version}
            for runner, (centiseconds, link) in sorted(row.entries.items())]


def _new_approach(row, name) -> dict:
    return {"ids": sorted(row.ids), "name": name,
            "times": ({row.version: row.best_cs}
                      if row.version and row.best_cs is not None else {}),
            "best_cs": row.best_cs, "best_runner": row.best_runner,
            "ideal_cs": row.ideal_cs, "fill_rate": row.fill_rate,
            "entries": _entries(row)}


def _merge(approach, row) -> None:
    approach["ids"] = sorted(set(approach["ids"]) | set(row.ids))
    if row.best_cs is not None:
        approach["times"][row.version] = row.best_cs
        if approach["best_cs"] is None or row.best_cs < approach["best_cs"]:
            approach["best_cs"], approach["best_runner"] = row.best_cs, row.best_runner
    if row.ideal_cs is not None and approach["ideal_cs"] is None:
        approach["ideal_cs"] = row.ideal_cs
    approach["entries"].extend(_entries(row))


def _pack_approaches(rows) -> list:
    """Approach rows -> approaches, pairing (JP)/(US) siblings by base name.

    A row only merges into a sibling that does NOT already carry its version:
    two rows of the same version under one name are different approaches whose
    labels happen to collide, and merging them would silently drop one."""
    out, by_name = [], {}
    for row in rows:
        name = _base_name(row.label)
        sibling = by_name.get(name)
        if row.version and sibling is not None and row.version not in sibling["times"]:
            _merge(sibling, row)
            continue
        approach = _new_approach(row, name)
        out.append(approach)
        if row.version:
            by_name[name] = approach
    return out


def build(data: bytes, fetched_at: str, overrides: dict | None = None) -> dict:
    rows = read_rows(data)
    runners = sorted({runner for row in rows for runner in row.entries})
    targets, current = [], None
    for row in rows:
        if row.opens_target:
            label = _base_name(row.label)
            key = map_target(row.section, label)
            current = {"entity_key": key, "group": row.group,
                       "section": row.section, "label": label,
                       # BBH opens TWO targets called "Go on a Ghost Hunt",
                       # one per ROM version, so the version is part of a
                       # target's identity rather than decoration.
                       "version": row.version,
                       "miss_reason": (None if key else
                                       miss_reason(row.section, label, row.group)),
                       "_approach_rows": [], "subsections": []}
            targets.append(current)
        if current is None:
            continue                      # a row before any target opened
        if row.kind == "approach":
            current["_approach_rows"].append(row)
        else:
            current["subsections"].append(
                {"ids": sorted(row.ids), "name": row.label,
                 "best_cs": row.best_cs, "best_runner": row.best_runner,
                 "entries": _entries(row)})
    for target in targets:
        target["approaches"] = _pack_approaches(target.pop("_approach_rows"))
    payload = {"schema_version": SCHEMA_VERSION,
               "sheet_revision": log_revision(data),
               "fetched_at": fetched_at,
               "runners": runners,
               # Where the sheet's own bold disagrees with the structural
               # boundary. Reported rather than raised: the sheet legitimately
               # restyles (nine CCM rows lost bold overnight on 2026-08-05),
               # and a silent divergence is how nine targets would vanish.
               # `label` is version-STRIPPED, matching how a target is named
               # in `targets` -- the raw row label would miss every drifted
               # target whose name carries a (JP)/(US) suffix.
               "styling_drift": [
                   {"row": row.row, "section": row.section,
                    "label": _base_name(row.label),
                    "opens_target": row.opens_target, "bold": row.bold}
                   for row in rows if row.opens_target != row.bold],
               "targets": targets}
    if overrides:
        # The human's audit outranks our classification, and it is applied
        # HERE rather than at read time so every consumer -- the snapshot, the
        # server, phase 2's fitter -- sees one corrected payload instead of
        # each having to remember to apply it.
        from sm64_events.library.audit import apply_overrides
        payload = apply_overrides(payload, overrides)
    return payload


def _items(target):
    return list(target["approaches"]) + list(target["subsections"])


def coverage(payload: dict) -> dict:
    targets = payload["targets"]
    by_reason = {}
    for target in targets:
        if target["entity_key"] is None:
            reason = target["miss_reason"] or "unknown"
            by_reason[reason] = by_reason.get(reason, 0) + 1
    entries = [entry for target in targets
               for item in _items(target) for entry in item["entries"]]
    return {"targets": len(targets),
            "mapped": sum(1 for t in targets if t["entity_key"]),
            "unmapped": sum(1 for t in targets if not t["entity_key"]),
            "entities": len({t["entity_key"] for t in targets if t["entity_key"]}),
            "by_reason": by_reason,
            "approaches": sum(len(t["approaches"]) for t in targets),
            "subsections": sum(len(t["subsections"]) for t in targets),
            "entries": len(entries),
            "videos": sum(1 for e in entries if e["video"]),
            "runners": len(payload["runners"])}
