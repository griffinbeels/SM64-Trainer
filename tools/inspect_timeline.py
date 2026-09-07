"""What did the system HOLD for each frame of the replay he is looking at?

Round 32, item 44 -- his ask verbatim: "We need instrumentation so that you
can inspect exactly what data was held for each frame of the replay I'm
looking at, and then be able to compare that to the input timeline
displayed."

For an attempt's cached clip this prints, per PANEL frame (the timeline's
"FRAME 31 / 399" readout, which is 0-based = the track axis; verified
against his screenshots at panels 110/111), everything each layer held:

  - the video slots whose pictures the map says show that frame,
  - the picture-ledger rows composed inside those slots' walls -- their
    own frame stamps, so a map-vs-ledger disagreement is visible per row,
  - the input track's pad for that frame (stick + buttons), plus its
    neighbours for one-frame-off comparisons against a screenshot.

--audit walks the WHOLE clip instead: per encoded picture, the ledger row
nearest its wall (after the clip's own median bias) and the residual
between that row's stamp and the aligned map's answer -- the local wobble
the global anchor cannot see -- plus the row rate (the game presents ~30
pictures/s; more rows than that means the capture-side dedup fired on
something that is not a new game picture).

Reproduces the panel's own lookup (inputtimeline.js::mappedFrameAtTime:
slot = floor(t*fps), axis via the payload's stretches) rather than
restating it loosely -- the comparison is only honest if it walks the same
door. Read-only; safe beside a live session.

Usage:
  uv run python tools/inspect_timeline.py --attempt 4518 --frames 30-34,42,101
  uv run python tools/inspect_timeline.py --attempt 4518 --audit
"""
import argparse
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sm64_events.inputs.runs import stretches
from sm64_events.inputs.track import track_for_attempt
from sm64_events.memory import addresses as A
from sm64_events.storage.db import Database

CLIP_FPS = 60.0

def slot_time(slot, frame_times):
    """The wall-media time of video slot `slot`. A picture-feed clip
    (item 38) is VFR and carries every frame's own time; only a CFR
    clip sits on the 60 Hz grid. Reading a VFR clip on the grid was
    the tool artifact that faked a ~55-frame stamp gap (2026-09-02)."""
    if frame_times and 0 <= slot < len(frame_times):
        return frame_times[slot]
    return (slot + 0.5) / CLIP_FPS

def slot_of_time(ts, frame_times):
    """The video slot whose span contains media time `ts`."""
    if frame_times:
        import bisect
        return max(0, bisect.bisect_right(frame_times, ts) - 1)
    return int(ts * CLIP_FPS)


def parse_frames(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            lo, hi = part.split("-")
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return out


def buttons_named(mask: int) -> str:
    names = [name for bit, name in A.BUTTON_BITS if mask & bit]
    return "+".join(names) if names else "none"


def pad_text(frame) -> str:
    return (f"stick ({frame.stick_x:+4d},{frame.stick_y:+4d})  "
            f"buttons {buttons_named(frame.buttons)}")


def game_frame_of(axis: int, seams) -> int | None:
    for axis_start, raw_start, length in seams:
        if axis_start <= axis < axis_start + length:
            return raw_start + (axis - axis_start)
    return None


def axis_of(raw: int, seams) -> int | None:
    for axis_start, raw_start, length in seams:
        if raw_start <= raw < raw_start + length:
            return axis_start + (raw - raw_start)
    return None


def load_world(attempt_id: int | None):
    db = Database(Path("data") / "tracker.db")
    attempts = {a.id: a for a in db.attempts()}
    if attempt_id is None:
        candidates = sorted(
            Path("data/replay_buffer/clips").glob("clip_attempt_*.json"),
            key=lambda p: p.stat().st_mtime)
        attempt_id = int(candidates[-1].stem.rsplit("_", 1)[1])
    sidecar_path = (Path("data/replay_buffer/clips")
                    / f"clip_attempt_{attempt_id}.json")
    side = json.loads(sidecar_path.read_text())
    attempt = attempts[attempt_id]
    track = track_for_attempt(db.inputs, attempt)
    seams = stretches(track)
    return attempt_id, side, track, seams


def picture_spans(frame_map) -> list[tuple[int, int, int]]:
    """(first_slot, slot_count, raw_frame) per run of equal map values."""
    spans: list[tuple[int, int, int]] = []
    for slot, raw in enumerate(frame_map):
        if raw is None:
            continue
        if spans and spans[-1][2] == raw \
                and slot == spans[-1][0] + spans[-1][1]:
            spans[-1] = (spans[-1][0], spans[-1][1] + 1, raw)
        else:
            spans.append((slot, 1, raw))
    return spans


def detail(attempt_id, side, track, seams, panel_frames) -> None:
    frame_map = side.get("frame_map") or []
    frame_times = side.get("frame_times")
    rows = side.get("picture_ledger") or []
    pads = dict(track)
    # The map's own health, in the terms the ONE path still has: which
    # source built it, how much of the clip the feed log accounted for, and
    # how many rows the plugin could not call exact. The aligner's
    # offset/fit fields it used to print died with the aligner (2026-09-05).
    match = side.get("feed_match") or {}
    print(f"attempt {attempt_id}: map source {side.get('frame_map_source')}"
          f", feed matched {match.get('matched')} of {match.get('frames')}"
          f", inexact rows {side.get('plugin_inexact_rows')}")
    for panel in panel_frames:
        axis = panel                # the panel's FRAME readout is 0-based
        raw = game_frame_of(axis, seams)
        print(f"\n== PANEL FRAME {panel}  (axis {axis}, game frame {raw})")
        if raw is None:
            print("   no game frame on the track's axis here")
            continue
        for offset in (-1, 0, 1):
            neighbour = pads.get(raw + offset)
            tag = {-1: "prev", 0: "THIS", 1: "next"}[offset]
            if neighbour is not None:
                print(f"   track {tag} f{raw + offset}: "
                      f"{pad_text(neighbour)}")
        slots = [slot for slot, shown in enumerate(frame_map)
                 if shown == raw]
        print(f"   map: slots {slots} show f{raw}")
        if not slots:
            continue
        lo = slot_time(slots[0] - 1, frame_times)
        hi = slot_time(slots[-1] + 2, frame_times)
        near = [row for row in rows if lo <= row["ts"] <= hi]
        for row in near:
            slot_of_row = slot_of_time(row["ts"], frame_times)
            mapped_there = (frame_map[slot_of_row]
                            if slot_of_row < len(frame_map) else None)
            drift = (row["frame"] - mapped_there
                     if mapped_there is not None and row["frame"] is not None
                     else None)
            # `exact` = the capture layer's own stamp (item 95): the frame
            # the plugin read inside Project64, never an inference.
            mark = "  [exact]" if row.get("exact") else ""
            pad = f"  pad {row['pad']}" if row.get("pad") is not None else ""
            print(f"   ledger ts {row['ts']:+8.4f}s (slot {slot_of_row}): "
                  f"stamped f{row['frame']}  map there f{mapped_there}"
                  f"  stamp-map {drift:+d}{mark}{pad}" if drift is not None else
                  f"   ledger ts {row['ts']:+8.4f}s: stamped f{row['frame']}{mark}{pad}")


def audit(attempt_id, side, track, seams) -> None:
    frame_map = side.get("frame_map") or []
    rows = [row for row in (side.get("picture_ledger") or [])
            if row.get("frame") is not None]
    duration = side.get("duration_s") or 0
    print(f"attempt {attempt_id}: {len(rows)} ledger rows over "
          f"{duration:.1f}s = {len(rows) / duration:.1f}/s "
          f"(the game presents ~30/s)")
    gaps = [round(b["ts"] - a["ts"], 4)
            for a, b in itertools.pairwise(rows)]
    small = sum(1 for gap in gaps if gap < 0.020)
    print(f"row gaps: min {min(gaps):.4f}s  under-20ms {small} of "
          f"{len(gaps)} (a real picture change is ~33ms apart; closer "
          f"rows mean one presented picture landed TWO rows)")
    spans = picture_spans(frame_map)
    print(f"pictures in the encode (map spans): {len(spans)}; expected "
          f"~{duration * 30:.0f} game frames minus capture drops")
    residuals, mode, wobble_spans = _stamp_residuals(
        spans, rows, side.get("frame_map_offset", 0),
        side.get("frame_times"))
    print(f"stamp-vs-map residuals per picture: "
          f"{dict(sorted(residuals.items()))}  (mode {mode:+d})")
    axis0 = seams[0][1] if seams else 0
    for lo_slot, hi_slot, residual in wobble_spans[:40]:
        lo_axis = axis_of(frame_map[lo_slot], seams)
        print(f"  wobble {residual:+d} over slots {lo_slot}..{hi_slot}"
              f"  (panel frame ~{lo_axis if lo_axis is not None else '?'}"
              f", video {slot_time(lo_slot, side.get('frame_times')):.2f}s)")
    if len(wobble_spans) > 40:
        print(f"  ... and {len(wobble_spans) - 40} more")
    print(f"(residual 0 = the ledger row agrees with the aligned map; a "
          f"nonzero stretch is a place the panel would sit that many "
          f"frames off the screen)   [axis starts at raw f{axis0}]")


def _stamp_residuals(spans, rows, map_offset, frame_times=None):
    """Per picture: the nearest ledger stamp minus the aligned map's answer
    (after the clip's own median clock bias), plus the runs of non-modal
    residual -- the wobble stretches the global anchor cannot see."""
    times = [row["ts"] for row in rows]
    stamps = [row["frame"] for row in rows]

    def nearest(wall):
        best, best_gap = None, None
        for index, ts in enumerate(times):
            gap = abs(ts - wall)
            if best_gap is None or gap < best_gap:
                best, best_gap = index, gap
        return best

    deltas = sorted(
        times[found] - slot_time(first_slot, frame_times)
        for first_slot, _count, _raw in spans
        if (found := nearest(slot_time(first_slot, frame_times))) is not None)
    bias = deltas[len(deltas) // 2] if deltas else 0.0
    per_picture = []
    for first_slot, count, raw in spans:
        found = nearest(slot_time(first_slot, frame_times) + bias)
        if found is not None:
            per_picture.append(
                (first_slot, count, stamps[found] - map_offset - raw))
    residuals: dict[int, int] = {}
    for _slot, _count, residual in per_picture:
        residuals[residual] = residuals.get(residual, 0) + 1
    mode = residuals_mode(residuals)
    wobble_spans = []
    current = None
    for first_slot, count, residual in per_picture:
        if residual != mode:
            if current is None or residual != current[2]:
                if current is not None:
                    wobble_spans.append(tuple(current))
                current = [first_slot, first_slot + count, residual]
            else:
                current[1] = first_slot + count
        elif current is not None:
            wobble_spans.append(tuple(current))
            current = None
    if current is not None:
        wobble_spans.append(tuple(current))
    return residuals, mode, wobble_spans


def residuals_mode(counts: dict) -> int:
    return max(counts.items(), key=lambda pair: pair[1])[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", type=int, default=None)
    parser.add_argument("--frames", type=str, default=None,
                        help="panel frames, e.g. 30-34,42,101")
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    attempt_id, side, track, seams = load_world(args.attempt)
    if args.audit or not args.frames:
        audit(attempt_id, side, track, seams)
    if args.frames:
        detail(attempt_id, side, track, seams, parse_frames(args.frames))


if __name__ == "__main__":
    main()
