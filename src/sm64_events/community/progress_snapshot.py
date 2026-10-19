"""Current saved PB facts, portable without local IDs or private definitions.

This is a full replacement, not an achievement feed. The existing selector owns
save/Undo ordering; the existing rank and Scorecard readers consume the result.
"""
from datetime import datetime, timezone
from functools import lru_cache
import json

from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.ghost.sharing_targets import bind, binding_index, canonical, checked_target, describe, segment_signature
from sm64_events.tracking.views import latest_pbs_by_strategy

from .config import CommunityError
from .outbox import checked_strategy

MAX_ROWS = 4096
MAX_BYTES = 1024 * 1024
SOURCE_KINDS = frozenset({"played", "manual", "sheet", "imported", "legacy"})
ROW_FIELDS = frozenset({"target", "strategy", "clock", "frames", "game_version", "pb_version", "save_order",
                        "source_kind", "source_cs", "achieved_utc", "saved_utc"})
COVERAGE_FIELDS = frozenset({"total_rows", "shared_rows", "withheld_rows", "unsupported_rows", "complete"})


def _utc(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if stamp.tzinfo else None
    except (ValueError, OverflowError):
        return None


@lru_cache(maxsize=1)
def public_segments():
    """Canonical public names/signatures, independent of a local edited label."""
    path = bundled_defaults_seed()
    if path is None:
        return {}
    seed = json.loads(path.read_text(encoding="utf-8"))
    definitions = [{**row, "id": index} for index, row in enumerate(seed["segments"], 1)]
    return {row["seed_key"]: {"kind": "segment", "entity_key": "1", "name": row["name"],
                             "signature": segment_signature(row, definitions)} for row in definitions}


def _portable_target(row, definitions, public, bindings):
    segment = row["segment_id"]
    if segment is None:
        target = describe("star", f"{row['course_id']}-{row['star_id']}", None, ())
        target.pop("strat", None)
        return target
    definition = next((item for item in definitions if item["id"] == segment), None)
    if definition is None or not definition.get("enabled", True):
        raise ValueError("Unavailable segment")
    descriptor = public.get(definition.get("seed_key"))
    if descriptor is None:
        return None
    signature = segment_signature(definition, definitions)
    if signature != descriptor["signature"]:
        return None  # An edited default is personal, even while retaining seed_key.
    if bindings.get(signature) != [str(segment)]:
        raise ValueError("Ambiguous segment")
    return dict(descriptor)


def _source(row, attempt):
    imported = row.get("imported_from")
    if imported == "manual":
        return "manual"
    if isinstance(imported, str) and imported.startswith("sheet:"):
        return "sheet"
    if imported or attempt is not None and attempt.timed_by == "imported":
        return "imported"
    return "played" if attempt is not None else "legacy"


def _project_row(row, target, attempt, order):
    source = _source(row, attempt)
    return {"target": target, "strategy": row["strat_tag"], "clock": row["timer_mode"], "frames": row["frames"],
            "game_version": row.get("game_version"), "pb_version": row.get("pb_version", row.get("game_version")),
            "save_order": order, "source_kind": source, "source_cs": row.get("source_cs"),
            "achieved_utc": _utc(attempt.ended_utc) if source == "played" else None,
            "saved_utc": _utc(row.get("saved_utc"))}


def project(service):
    """Read on the service's mutation loop, then publish only the returned value.

    Raw game_version and joined pb_version deliberately remain separate: existing
    ranking/Scorecard readers do not give those two fields identical semantics.
    The caller owns consent, account, publisher fencing and capture time.
    """
    if service.db is None:
        raise CommunityError("progress_unavailable", "Saved progress is unavailable.")
    selected = sorted(latest_pbs_by_strategy(service.db.pbs()).values(), key=lambda row: row["id"])
    definitions, public = service.db.segment_defs(), public_segments()
    bindings = binding_index(definitions)
    rows, withheld, unsupported = [], 0, 0
    for row in selected:
        try:
            target = _portable_target(row, definitions, public, bindings)
            if target is None:
                withheld += 1
                continue
            attempt = service.db.attempt(row["attempt_id"]) if row.get("attempt_id") is not None else None
            published = _project_row(row, target, attempt, len(rows) + 1)
            _check_row(published)
            rows.append(published)
        except (ValueError, KeyError, TypeError, AttributeError):
            unsupported += 1
    ranks = getattr(service, "ranks", None)
    default_rom = getattr(ranks, "grading_version", None) or service.effective_game_version()
    coverage = {"total_rows": len(selected), "shared_rows": len(rows), "withheld_rows": withheld,
                "unsupported_rows": unsupported, "complete": withheld + unsupported == 0}
    return checked_snapshot({"schema": 1, "default_rom": default_rom, "rows": rows, "coverage": coverage})


def _check_row(row):
    if not isinstance(row, dict) or set(row) != ROW_FIELDS:
        raise CommunityError("invalid_progress_snapshot")
    target = checked_target(row["target"])
    if (set(target) != ({"kind", "entity_key", "name", "signature"} if target["kind"] == "segment" else {"kind", "entity_key", "name"})
            or target["kind"] == "segment" and target["entity_key"] != "1"
            or row["clock"] not in ("igt", "rta") or row["game_version"] not in (None, "us", "jp")
            or row["pb_version"] not in (None, "us", "jp")
            or row["game_version"] is not None and row["pb_version"] != row["game_version"]
            or type(row["frames"]) is not int or not 1 <= row["frames"] <= 2592000
            or type(row["save_order"]) is not int or not 1 <= row["save_order"] <= 2147483647
            or row["source_kind"] not in SOURCE_KINDS):
        raise CommunityError("invalid_progress_snapshot")
    if row["strategy"] is not None:
        checked_strategy(row["strategy"])
    if row["source_cs"] is not None and (type(row["source_cs"]) is not int or not 1 <= row["source_cs"] <= 8640000):
        raise CommunityError("invalid_progress_snapshot")
    if any(row[field] is not None and _utc(row[field]) is None for field in ("achieved_utc", "saved_utc")):
        raise CommunityError("invalid_progress_snapshot")
    if row["source_kind"] != "played" and row["achieved_utc"] is not None:
        raise CommunityError("invalid_progress_snapshot")


def checked_snapshot(content):
    """Validate a received or locally constructed bounded full replacement."""
    if (not isinstance(content, dict) or set(content) != {"schema", "default_rom", "rows", "coverage"}
            or type(content["schema"]) is not int or content["schema"] != 1 or content["default_rom"] not in ("us", "jp")
            or not isinstance(content["rows"], list)):
        raise CommunityError("invalid_progress_snapshot")
    if len(content["rows"]) > MAX_ROWS:
        raise CommunityError("progress_snapshot_too_large", "Saved progress exceeds the publication limit.")
    coverage = content["coverage"]
    if (not isinstance(coverage, dict) or set(coverage) != COVERAGE_FIELDS or type(coverage["complete"]) is not bool
            or any(type(coverage[key]) is not int or not 0 <= coverage[key] <= 2147483647 for key in COVERAGE_FIELDS - {"complete"})
            or coverage["shared_rows"] != len(content["rows"])
            or coverage["total_rows"] != coverage["shared_rows"] + coverage["withheld_rows"] + coverage["unsupported_rows"]
            or coverage["complete"] != (coverage["withheld_rows"] + coverage["unsupported_rows"] == 0)):
        raise CommunityError("invalid_progress_snapshot")
    seen, orders = set(), set()
    try:
        for row in content["rows"]:
            _check_row(row)
            if row["save_order"] > MAX_ROWS:
                raise ValueError("Invalid save order")
            target = row["target"]
            identity = (target["kind"], target.get("signature", target["entity_key"]), row["strategy"], row["clock"], row["game_version"])
            if identity in seen or row["save_order"] in orders:
                raise ValueError("Duplicate saved slot")
            seen.add(identity)
            orders.add(row["save_order"])
        encoded = canonical(content)
    except (ValueError, TypeError, KeyError):
        raise CommunityError("invalid_progress_snapshot") from None
    if len(encoded) > MAX_BYTES:
        raise CommunityError("progress_snapshot_too_large", "Saved progress exceeds the publication limit.")
    return json.loads(encoded)


def resolve_rows(content, definitions=()):
    """Adapt public facts to the canonical PB readers; never write local PBs."""
    content = checked_snapshot(content)
    index = binding_index(definitions)
    rows, unbound = [], 0
    for row in sorted(content["rows"], key=lambda item: item["save_order"]):
        target = row["target"]
        key, reason = bind(target, index=index)
        if reason:
            unbound += 1
            continue
        course, star = map(int, key.split("-")) if target["kind"] == "star" else (None, None)
        rows.append({"id": row["save_order"], "course_id": course, "star_id": star,
                     "segment_id": int(key) if target["kind"] == "segment" else None,
                     "strat_tag": row["strategy"], "timer_mode": row["clock"], "frames": row["frames"],
                     "game_version": row["game_version"], "pb_version": row["pb_version"], "attempt_id": None,
                     "saved_utc": row["saved_utc"], "imported_from": None if row["source_kind"] == "played" else row["source_kind"]})
    return rows, unbound
