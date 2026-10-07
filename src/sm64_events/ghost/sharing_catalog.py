"""Explicit-click discovery/export of local recordings; no frame-loop work."""
from datetime import datetime

from sm64_events.ghost.portable import MAX_ENTRIES, checked_hash, make_entry
from sm64_events.ghost.sharing_targets import describe
from sm64_events.inputs.service import entity_key_of
from sm64_events.tracking.caveats import save_state_run


def _timing(attempt, kind):
    clock = "rta" if kind == "segment" or attempt.igt_frames is None else "igt"
    return getattr(attempt, f"{clock}_frames"), clock


def _local_row(attempt, definitions):
    kind, key = entity_key_of(attempt)
    target = describe(kind, key, attempt.strat_tag, definitions)
    frames, clock = _timing(attempt, kind)
    return {"id": f"attempt:{attempt.id}", "source": "local", "attempt_id": attempt.id,
            "import_id": None, "author": "You", "kind": kind, "entity_key": key,
            "strat": attempt.strat_tag, "target": target, "target_name": target["name"],
            "frames": frames, "clock": clock, "recorded_utc": attempt.started_utc,
            "practice": save_state_run(attempt) or getattr(attempt, "valid", None) is False,
            "game_version": getattr(attempt, "game_version", None),
            "outcome": attempt.outcome, "available": True, "reason": None}


def local_rows(replay, database):
    """Keep saved poses and buffer-covered attempts. Export verifies exact endpoints."""
    definitions = database.segment_defs()
    saved_root = replay.cfg.save_root / "native"
    saved_ids = {int(path.stem[8:]) for path in saved_root.glob("attempt_*.ghost")
                 if path.stem[8:].isdecimal()}
    # Video-side native poses can be elsewhere beneath save_root. This only
    # indexes saved names, without decoding every file in a library read.
    from sm64_events.replay.service import saved_attempt_ids
    saved_ids.update(saved_attempt_ids(replay.cfg.save_root, include_native=False))
    archive = getattr(getattr(replay, "recorder", None), "pose_archive", None)
    coverage = archive.status() if archive is not None else {}
    first, last = coverage.get("first_utc"), coverage.get("last_utc")
    result = []
    for attempt in reversed(database.attempts()):
        if getattr(attempt, "native_clock_frame", None) is None or getattr(attempt, "closed_frame", None) is None:
            continue
        buffered = False
        if first is not None and last is not None:
            try:
                start = datetime.fromisoformat(attempt.native_clock_utc.replace("Z", "+00:00")).timestamp()
                end = datetime.fromisoformat(attempt.ended_utc.replace("Z", "+00:00")).timestamp()
                buffered = first <= start <= end <= last
            except (ValueError, AttributeError):
                pass
        if attempt.id not in saved_ids and not buffered:
            continue
        try:
            row = _local_row(attempt, definitions)
        except ValueError:
            continue
        if row["frames"] is not None:
            result.append({**row, "saved": attempt.id in saved_ids})
    return result


def checked_selection(selected):
    if not isinstance(selected, dict) or set(selected) - {"attempts", "imports"}:
        raise ValueError("Choose which ghosts to export.")
    attempts, imports = selected.get("attempts", []), selected.get("imports", [])
    if not isinstance(attempts, list) or not isinstance(imports, list):
        raise ValueError("Choose which ghosts to export.")
    if any(type(number) is not int or number <= 0 for number in attempts):
        raise ValueError("Choose recorded attempts to export.")
    for number in imports:
        checked_hash(number)
    if len(set(attempts)) != len(attempts) or len(set(imports)) != len(imports):
        raise ValueError("Each ghost can be exported once.")
    if not 1 <= len(attempts) + len(imports) <= MAX_ENTRIES:
        raise ValueError(f"Choose between 1 and {MAX_ENTRIES} ghosts to export.")
    return attempts, imports


def export_rows(sharing, selected, author, step):
    attempts, imports = checked_selection(selected)
    entries, skipped = [], []
    definitions = sharing.database.segment_defs()
    sources = [("local", number) for number in attempts] + [("import", number) for number in imports]
    for index, (source, number) in enumerate(sources):
        try:
            if source == "local":
                attempt = sharing.replay.attempt(number)
                row = _local_row(attempt, definitions)
                found = sharing.replay.preserve_native(number)
                if not found.get("path"):
                    raise ValueError(found.get("native_ghost_reason") or "Its Mario poses are no longer recorded.")
                # Re-read the committed asset through the existing provenance
                # validator; a path returned by a save alone is not authority.
                asset = sharing.replay.find_native(number)
                if asset is None:
                    raise ValueError("Its saved Mario poses could not be validated.")
                # The ghost as it plays here, inputs from its track included.
                path, _manifest = sharing.replay.native_with_inputs(number, asset)
                entry = make_entry(path, author=author, target=row["target"], frames=row["frames"],
                                   clock=row["clock"], recorded_utc=row["recorded_utc"],
                                   practice=row["practice"], outcome=row["outcome"],
                                   game_version=row["game_version"])
            else:
                path, _manifest = sharing.export_asset(number)
                entry = sharing.portable_entry(number)  # preserve original credit and portable target
            entries.append((entry, path))
        except (LookupError, ValueError, OSError) as error:
            skipped.append({"source": source, "id": number, "reason": str(error)})
        step(.5 * (index + 1) / len(sources), f"Saved {index + 1} of {len(sources)} selected ghosts.")
    return entries, skipped
