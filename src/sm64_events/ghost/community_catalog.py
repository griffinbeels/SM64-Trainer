"""Export canonical integer bins using the trainer's existing rank derivation.

The cloud consumes these results, never a second handwritten ranking formula.
The native recording limit bounds the slowest eligible frame count. Adjacent
bins with identical cutoffs are unreachable and remain absent from matches.
"""
import hashlib
import json

from sm64_events.ghost.adaptive import MAX_STEP, _progress, _range, _read_context, rank_entity_key
from sm64_events.ghost.sharing_targets import binding_index, describe
from sm64_events.ranks.calibration import resolve_curve

MAX_FRAMES = 100000
GHOST_CEILING_PERCENT = 10
"""How far past the slowest named cutoff a run may be and still become a ghost.

His rule, 2026-10-13: "Maybe it's the capless 5 threshold plus 10%? So, like,
in this case it would be 07"00 plus 10%. Anything above that DOES NOT CREATE A
GHOST. ... We shouldn't tell the user that their ghost was ignored from the
sharing system. ... Anything within the threshold should be shared, though."
The slowest named cutoff is rung 1 of the strategy ladder
(`slowest_cutoff_frames`); the cloud applies the identical integer rule.
"""


def _stepper(model, scope):
    """One memoized grading per frame count, shared by every rung's search."""
    values = {}

    def step(frame):
        if frame not in values:
            value = _progress(model, frame, scope)
            values[frame] = value["step"] if value else -1
        return values[frame]
    return step


def _slowest_at(step, rung, max_frames):
    """The slowest frame count still graded at `rung` or better; None when unreachable."""
    if step(1) < rung:
        return None
    low, high = 1, max_frames
    while low < high:
        middle = (low + high + 1) // 2
        if step(middle) >= rung:
            low = middle
        else:
            high = middle - 1
    return low


def model_cutoffs(model, scope, *, max_frames=MAX_FRAMES):
    if not model:
        return []
    step = _stepper(model, scope)
    result = []
    for rung in range(MAX_STEP + 1):
        frames = _slowest_at(step, rung, max_frames)
        if frames is not None:
            result.append({"step": rung, "frames": frames})
    return result


def slowest_cutoff_frames(model, scope, *, max_frames=MAX_FRAMES):
    """The slowest named cutoff: the last frame count at rung 1 (rung 0 is open-ended)."""
    if not model:
        return None
    return _slowest_at(_stepper(model, scope), 1, max_frames)


def within_ghost_ceiling(frames, slowest):
    """True when a run may become a ghost; integer-exact, so the cloud agrees."""
    return slowest is None or frames * 100 <= slowest * (100 + GHOST_CEILING_PERCENT)


def strategy_ghost_cutoff(ranks, identity):
    """The slowest named cutoff of the identity's own strategy ladder.

    Always the strategy ladder, whichever scope the practice card shows. None
    when the ladder or clock cannot answer: admission refuses those itself.
    """
    if not identity.strategy:
        return None
    with _read_context(ranks):
        key = rank_entity_key(identity)
        if ranks.clock_for(key) != identity.clock:
            return None
        return slowest_cutoff_frames(ranks.ladder_cs(key, identity.strategy, identity.game_version), "strategy")


def within_strategy_ceiling(ranks, identity, frames):
    """Whether a finished run may become a ghost on its own strategy's ladder."""
    return type(frames) is not int or within_ghost_ceiling(frames, strategy_ghost_cutoff(ranks, identity))


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
