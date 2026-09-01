"""The offset, measured with NO threshold in the way.

Two continuous signals instead of two boolean series:
  video[slot]  = how much the digit ink moved between this slot and the last
  track[slot]  = how much the PAD moved between the game frames the map
                 assigns those same two slots, under a candidate shift
Correlate them. The argmax shift is the clip's true offset, and the peak's
height over its neighbours says whether to believe it. A threshold that picks
the wrong split (the bug this replaces) cannot distort a correlation.
"""
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(r"C:\Users\griff\Desktop\code\sm64_tracker\.claude\worktrees\input-timeline")
sys.path.insert(0, str(ROOT / "src"))
from sm64_events.inputs.track import track_with_lead
from sm64_events.replay import mapalign
from sm64_events.storage.db import Database

ATTEMPT = int(sys.argv[1]) if len(sys.argv) > 1 else 5431
side_path = ROOT / f"data/replay_buffer/clips/clip_attempt_{ATTEMPT}.json"
side = json.loads(side_path.read_bytes())
frame_map = side["frame_map"]
seen = [v for v in frame_map if v is not None]

clip = side_path.with_suffix(".mp4")
width = mapalign.probe_width("ffmpeg", clip)
pixels = mapalign.decode_region("ffmpeg", clip, mapalign.region_for_width(width))
red, blue = pixels[..., 0], pixels[..., 2]
lit = ((red > mapalign.INK_MIN_RED) & (blue < mapalign.INK_MAX_BLUE)
       & (red - blue > mapalign.INK_MIN_WARMTH))
flat = lit.reshape(len(lit), -1)
video = (flat[1:] != flat[:-1]).sum(axis=1).astype(np.float64)   # index = slot-1

db = Database(ROOT / "data" / "tracker.db")
attempt = next(a for a in db.attempts() if a.id == ATTEMPT)
frames, lead = track_with_lead(db.inputs, attempt, span=(min(seen), max(seen)))
pads = {n: (abs(f.stick_x), abs(f.stick_y)) for n, f in frames}

slots = min(len(lit), len(frame_map))
# Drop the washed-out reset frames: no ink at all means nothing to compare.
ink = flat.sum(axis=1)
usable = [s for s in range(1, slots) if ink[s] > 0 and ink[s - 1] > 0]
print(f"attempt {ATTEMPT}: {slots} slots, {len(usable)} usable boundaries "
      f"({slots - 1 - len(usable)} dropped for blank ink)")


def track_signal(shift):
    out = []
    for slot in usable:
        here, before = frame_map[slot], frame_map[slot - 1]
        a = pads.get(None if here is None else here + shift)
        b = pads.get(None if before is None else before + shift)
        out.append(np.nan if a is None or b is None
                   else abs(a[0] - b[0]) + abs(a[1] - b[1]))
    return np.array(out, dtype=np.float64)


observed = np.array([video[s - 1] for s in usable])
print("\nshift   pearson r   (against how much the digit ink moved)")
scores = {}
for shift in range(-15, 16):
    predicted = track_signal(shift)
    ok = ~np.isnan(predicted)
    if ok.sum() < 100 or predicted[ok].std() == 0:
        continue
    r = float(np.corrcoef(observed[ok], predicted[ok])[0, 1])
    scores[shift] = r
    print(f"{shift:+4d}    {r:6.3f}   {'#' * max(0, int(r * 60))}")

best = max(scores, key=scores.get)
runner = max((s for s in scores if abs(s - best) > 1), key=scores.get)
print(f"\nBEST SHIFT {best:+d}  (r={scores[best]:.3f});  "
      f"best outside +-1 is {runner:+d} (r={scores[runner]:.3f});  "
      f"margin {scores[best] - scores[runner]:.3f}")

print("\n--- per 250-slot window: does the offset DRIFT across the clip?")
for lo in range(1, slots, 250):
    hi = min(lo + 250, slots)
    window = [i for i, s in enumerate(usable) if lo <= s < hi]
    if len(window) < 60:
        continue
    local = {}
    for shift in range(-15, 16):
        predicted = track_signal(shift)[window]
        obs = observed[window]
        ok = ~np.isnan(predicted)
        if ok.sum() < 40 or predicted[ok].std() == 0:
            continue
        local[shift] = float(np.corrcoef(obs[ok], predicted[ok])[0, 1])
    if not local:
        continue
    top = max(local, key=local.get)
    print(f"  slots {lo:>4}-{hi:<4} ({lo / 59.987:5.1f}-{hi / 59.987:5.1f}s): "
          f"best {top:+3d} (r={local[top]:.3f})   "
          f"whole-clip {best:+d} gives r={local.get(best, float('nan')):.3f}")
