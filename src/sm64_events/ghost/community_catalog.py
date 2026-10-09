"""Export canonical integer bins using the trainer's existing rank derivation.

The cloud consumes these results, never a second handwritten ranking formula.
The native recording limit bounds the slowest eligible frame count. Adjacent
bins with identical cutoffs are unreachable and remain absent from matches.
"""
import hashlib
import json

from sm64_events.ghost.adaptive import MAX_STEP, _progress, _range, rank_entity_key
from sm64_events.ghost.sharing_targets import binding_index, describe
from sm64_events.ranks.calibration import resolve_curve

MAX_FRAMES = 100000


def model_cutoffs(model, scope, *, max_frames=MAX_FRAMES):
    if not model:
        return []
    values = {}

    def step(frame):
        if frame not in values:
            value = _progress(model, frame, scope)
            values[frame] = value["step"] if value else -1
        return values[frame]

    result = []
    for rung in range(MAX_STEP + 1):
        if step(1) < rung:
            continue
        low, high = 1, max_frames
        while low < high:
            middle = (low + high + 1) // 2
            if step(middle) >= rung:
                low = middle
            else:
                high = middle - 1
        result.append({"step": rung, "frames": low})
    return result


def export_catalog(ranks, definitions=()):
    entries = []
    bindings = binding_index(definitions)
    with ranks.read_context():
        for key in sorted(ranks.graded_entities()):
            if key.startswith("star:"):
                _, course, star = key.split(":")
                kind, entity = "star", f"{course}-{star}"
            elif key.startswith("segment:"):
                kind, entity = "segment", key.split(":")[1]
            else:
                continue
            try:
                target = describe(kind, entity, None, definitions)
            except ValueError:
                continue
            if kind == 'segment' and bindings.get(target['signature']) != [entity]:
                continue  # Disabled or ambiguous segments cannot bind safely on another client.
            for version in ("us", "jp"):
                strategies = []
                for name in sorted(ranks.strategies(key)):
                    cutoffs = model_cutoffs(ranks.ladder_cs(key, name, version), "strategy")
                    if cutoffs:
                        strategies.append({"name": name, "cutoffs": cutoffs})
                overall = model_cutoffs(resolve_curve(ranks, key, version), "overall")
                if strategies:
                    entries.append({"target": target, "clock": ranks.clock_for(key),
                                    "game_version": version, "strategies": strategies,
                                    "overall_cutoffs": overall})
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return {"v": 1, "generation": hashlib.sha256(encoded).hexdigest(), "entries": entries}


def match_windows(ranks, identity, pb, options):
    """Whole local rank bands plus the whole baseline bin; never the exact PB."""
    key = rank_entity_key(identity)
    with ranks.read_context():
        model = (resolve_curve(ranks, key, identity.game_version) if identity.scope == "overall"
                 else ranks.ladder_cs(key, identity.strategy, identity.game_version))
        cutoffs = {row["step"]: row["frames"] for row in model_cutoffs(model, identity.scope)}
    if not cutoffs:
        return ()
    low, high = _range(pb, options)
    base = pb["step"] if pb else 0
    result = []
    for first, last in ((low, high), (base, base)):
        maximum = cutoffs.get(first)
        next_frames = cutoffs.get(last + 1, 0)
        if maximum is not None and next_frames < maximum:
            result.append((next_frames + 1, maximum))
    return tuple(result)
