"""Pure comparison logic — no I/O, no db, no ffmpeg.

Three concerns:
- cache_name_for: the dedup identity. Two comparison rows referencing the
  same source_ref share ONE normalized file ("load once"). Content-addressed
  by a hash of the source string, NOT the row id.
- resolve_auto: the four-branch auto-selection priority (spec feature #6):
  most-recent saved > rank-standard suggestion > empty > no active strat.
- master_seek_time: offset-only sync — each stage seeks to its own in-point
  plus the shared master game-frame, aimed at the MIDDLE of the frame so
  float rounding can't straddle a frame boundary (same math as replay.js).
"""
import hashlib


def cache_name_for(source_ref: str) -> str:
    digest = hashlib.sha1(source_ref.encode("utf-8")).hexdigest()[:16]
    return f"{digest}.mp4"


def resolve_auto(saved: list[dict], suggestion: str | None,
                 strat: str | None) -> dict:
    """Pick what the comparison slot shows by default. `saved` is the list of
    comparison rows for (entity, strat); `suggestion` is the rank-standard
    video URL (or None). Priority: recent saved > suggestion > empty; with no
    active strat there is nothing to key on."""
    if not strat:
        return {"mode": "no_strat", "comparison": None}
    if saved:
        newest = max(saved, key=lambda c: c["last_used_utc"])
        return {"mode": "saved", "comparison": newest}
    if suggestion:
        return {"mode": "suggestion", "comparison": None}
    return {"mode": "empty", "comparison": None}


def master_seek_time(in_frame: int | None, master_frame: int,
                     game_fps: int = 30) -> float:
    return ((in_frame or 0) + master_frame + 0.5) / game_fps
