"""RE-RUN hop 5 on a clip that is already cut: the CLOCK join, one answer
per picture, and the pad display's audit -- then write the sidecar the way
extraction would have, so the open replay shows the corrected panel.

    uv run python tools/remap_clip.py --attempt 6592                 # rewrite the sidecar
    uv run python tools/remap_clip.py --attempt 6592 --dry-run       # print, write nothing
    uv run python tools/remap_clip.py --attempt 6592 --disagreements # ...and every contradicted slot
    uv run python tools/remap_clip.py --attempt 6592 --save          # ...and keep it as a SAVED replay

Round 32 item 93 (2026-09-04): his first three clips on the timer wiring
were cut by a reader that refused the pyramid (a display-lag drop read as a
spike) and crashed the pad audit (a None slot), and a cut clip keeps its
baked-in map forever -- `ReplayService.view` serves the sidecar it finds.
Re-recording is the only other way to see a reader fix on a clip, and it
loses the case that showed the bug. This tool needs only the cached .mp4
and its sidecar: the picture ledger it carries holds every stamp the join
needs ((gGlobalTimer, usamune_overall) per distinct picture), paired to
video slots by composition time (the feed log itself is not in the sidecar;
the pairing here is the same nearest-time join to within half a frame, and
reproduced the service's own count on 6611: 388 vs 392 mechanical, lag 1).

The scratch clip dies with the ring on the next server start (the recorder
wipes its scratch dir on init), so `--save` also copies the corrected clip
into the saved-replay tree under the same name the app's Save button would
use; `find_saved` indexes by that filename, and the next open of the
attempt's replay serves it from there.

It is also the regression instrument: `--dry-run` on every cached clip
prints the timer reading and the pad audit for each, with no server, no
emulator, and nothing written. Read-only in that mode.
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sm64_events.core.paths import bundled_ffmpeg, replays_root  # noqa: E402
from sm64_events.inputs.track import track_with_lead  # noqa: E402
from sm64_events.memory import addresses as A  # noqa: E402
from sm64_events.memory.addresses import course_name, star_name  # noqa: E402
from sm64_events.replay import padread, timerread  # noqa: E402
from sm64_events.replay.config import ReplayConfig  # noqa: E402
from sm64_events.replay.mapalign import decode_grey, picture_runs, quantised  # noqa: E402
from sm64_events.replay.service import DISPLAY_LAG_FRAMES, slug_filename  # noqa: E402
from sm64_events.storage.db import Database  # noqa: E402

HALF_A_FRAME_S = 1.0 / 60.0


def clock_pairs_from_ledger(meta: dict) -> tuple[list, list]:
    """(clock_pairs, prior) per video slot from the sidecar's picture ledger:
    each slot takes the distinct picture composed nearest its own timestamp,
    within half a frame; prior is the feed-log bookkeeping (stamp minus the
    display-lag constant) the timer bridges unreadable slots from."""
    rows = meta["picture_ledger"]
    bias = (meta.get("feed_match") or {}).get("bias_ms", 0.0) / 1000.0
    row_ts = [row["ts"] + bias for row in rows]
    pairs, prior = [], []
    for slot_time in meta["frame_times"]:
        nearest = min(range(len(rows)), key=lambda index: abs(row_ts[index] - slot_time))
        row = rows[nearest]
        if (abs(row_ts[nearest] - slot_time) <= HALF_A_FRAME_S
                and row.get("frame") is not None):
            prior.append(row["frame"] - DISPLAY_LAG_FRAMES)
            pairs.append((row["frame"], row["igt_overall"])
                         if row.get("igt_overall") is not None else None)
        else:
            prior.append(None)
            pairs.append(None)
    return pairs, prior


def pads_for(db: Database, attempt, frame_map: list):
    seen = [raw for raw in frame_map if raw is not None]
    frames, _lead = track_with_lead(
        db.inputs, attempt, span=(min(seen) - padread.BAND, max(seen) + padread.BAND))
    pads = {number: (frame.stick_x, frame.stick_y) for number, frame in frames}
    held = {number: frame.buttons & A.BUTTON_VALID_MASK for number, frame in frames}
    resets = ([attempt.anchor_frame]
              if getattr(attempt, "anchor_type", None) == "practice_reset"
              and attempt.anchor_frame is not None else [])
    return pads, held, resets


def audit_against_the_display(db, attempt, clip, frame_map, meta, ffmpeg,
                              mapping, pairs, show_disagreements):
    """The pad display's verdict on the timer map, scored before any
    alignment; None when the display could not be read on enough frames."""
    pads, held, resets = pads_for(db, attempt, frame_map)
    reading = padread.read_clip(clip, frame_map, pads, ffmpeg, held=held,
                                resets=resets, repeats=meta.get("repeats"))
    audit = getattr(reading, "audit", None) if reading is not None else None
    if audit is None:
        print("  pad audit: the display could not be read on enough frames")
        return None
    print(f"  pad audit: {audit.agree} of {audit.sure} sure slots agree "
          f"({100.0 * audit.agree / max(audit.sure, 1):.2f}%), {audit.nowhere} nowhere, "
          f"{len(audit.disagreements)} contradicted")
    if show_disagreements:
        exact = {slot for slot, pair in enumerate(pairs)
                 if pair is not None and mapping.frame_map[slot] is not None
                 and not mapping.reading.frozen[slot]
                 and mapping.reading.values[slot] is not None}
        times = meta["frame_times"]
        for slot, row, seen_text, says in audit.disagreements:
            kind = "mechanical" if slot in exact else "bridged"
            print(f"    slot {slot:5d}  {times[slot]:7.3f}s  {row}  screen {seen_text:5s}"
                  f"  map {says:>6}  [{kind}]")
    return audit


def rewrite_sidecar(sidecar: Path, meta: dict, frame_map: list, mapping, audit) -> dict:
    """The fields extraction writes when the timer answers (replay/service.py)."""
    for stale in ("frame_map_aligned", "frame_map_offset", "frame_map_fit",
                  "frame_map_windows", "frame_map_learned", "pad_reading",
                  "frame_map_read"):
        meta.pop(stale, None)
    meta["frame_map_base_source"] = meta.get("frame_map_source")
    meta["frame_map"] = frame_map
    meta["frame_map_source"] = "timer"
    meta["frame_map_mode"] = "timer+bridge" if mapping.bridged else "timer"
    meta["frame_map_inferred"] = mapping.bridged > 0
    meta["frame_map_quantised"] = True
    meta["timer_reading"] = mapping.as_dict()
    if audit is not None:
        meta["frame_map_read"] = True
        meta["pad_reading"] = audit.as_dict()
    sidecar.write_text(json.dumps(meta))
    print(f"  sidecar rewritten: {sidecar} -- re-open the replay to see it")
    return meta


def save_like_the_app(attempt, clip: Path, meta: dict) -> Path:
    """Copy the corrected clip into the saved-replay tree under the name the
    app's Save button uses, so it outlives the scratch wipe on restart."""
    ended_local = datetime.fromisoformat(attempt.ended_utc.replace("Z", "+00:00")).astimezone()
    dest_dir = replays_root() / ended_local.strftime("%Y-%m-%d") / f"session_{attempt.session_id}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    if attempt.segment_id is not None:
        course, star = f"segment-{attempt.segment_id}", ""
    else:
        course = course_name(attempt.course_id) if attempt.course_id is not None else "no-course"
        star = (star_name(attempt.course_id, attempt.star_id)
                if attempt.star_id is not None and attempt.course_id is not None else "no-star")
    dest = dest_dir / slug_filename(attempt, course, star)
    shutil.copy2(clip, dest)
    dest.with_suffix(".json").write_text(json.dumps({**meta, "fps": ReplayConfig().fps}))
    print(f"  saved as {dest}")
    return dest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--db", default="data/tracker.db")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the reading and the audit; write nothing")
    parser.add_argument("--disagreements", action="store_true",
                        help="list every slot the display contradicts, and "
                             "whether the timer named it or bridged it")
    parser.add_argument("--save", action="store_true",
                        help="also keep the corrected clip as a saved replay")
    args = parser.parse_args()
    clip = Path(f"data/replay_buffer/clips/clip_attempt_{args.attempt}.mp4")
    sidecar = clip.with_suffix(".json")
    if not clip.exists() or not sidecar.exists():
        print(f"no cached clip + sidecar at {clip}")
        return 2
    meta = json.loads(sidecar.read_text())
    if not meta.get("frame_times") or not meta.get("picture_ledger"):
        print("not a picture-feed clip with a picture ledger; nothing to re-join")
        return 2
    if all(row.get("exact") for row in meta["picture_ledger"]):
        # A capture-layer clip (item 95): the map IS the plugin's stamps;
        # there is no join to re-run. Score it against the oracle instead.
        print("a capture-layer clip: every row is the plugin's own stamp, the map is "
              "the rows -- nothing to re-join. Certify it with "
              f"`tools/score_oracle.py --attempt {args.attempt}`")
        return 0
    ffmpeg = str(bundled_ffmpeg() or "ffmpeg")
    pairs, prior = clock_pairs_from_ledger(meta)
    stamped = sum(1 for pair in pairs if pair is not None)
    print(f"attempt {args.attempt}: {len(prior)} slots, {stamped} carry a clock pair; "
          f"shipped map came from {meta.get('frame_map_source')}"
          + (f" (mode {meta.get('frame_map_mode')})" if meta.get("frame_map_mode") else ""))
    if not stamped:
        print("no (gGlobalTimer, usamune_overall) stamps: the clip predates the timer wiring")
        return 1
    mapping = timerread.read_clip(clip, prior, pairs, ffmpeg)
    if mapping is None:
        print("the timer path REFUSES this clip (see timerread.py's refusal guards)")
        return 1
    print("  timer:", mapping.as_dict())
    runs = picture_runs(decode_grey(ffmpeg, clip))
    frame_map = quantised(list(mapping.frame_map), runs) if runs else list(mapping.frame_map)

    db = Database(Path(args.db))
    attempt = next((row for row in db.attempts() if row.id == args.attempt), None)
    audit = None
    if attempt is None:
        print(f"  attempt {args.attempt} is not in {args.db}; no pad audit")
    else:
        audit = audit_against_the_display(db, attempt, clip, frame_map, meta, ffmpeg,
                                          mapping, pairs, args.disagreements)
    if args.dry_run:
        return 0
    meta = rewrite_sidecar(sidecar, meta, frame_map, mapping, audit)
    if args.save and attempt is not None:
        save_like_the_app(attempt, clip, meta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
