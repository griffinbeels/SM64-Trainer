"""Attempt -> span -> clip -> save.

Error taxonomy matches server/api.py:
  LookupError  -> 404 (no such attempt/clip)
  ValueError   -> 409 (no footage / span too short)
  RuntimeError -> 503 (db unavailable)
Anything else (e.g. codec failure on a corrupt segment) is a genuine 500 —
extract.py already guarantees no partial file survives those.
"""
import json
import logging
import re
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from sm64_events.core.timefmt import GAME_FPS, format_igt
from sm64_events.memory.addresses import course_name, star_name
from sm64_events.replay import mapalign, padread
from sm64_events.replay.feedmap import feed_map
from sm64_events.replay.extract import video_start_of
from sm64_events.replay.config import (ReplayConfig, save_settings,
                                       validate_settings)

log = logging.getLogger("sm64.replay")

_CLIP_NAME = "clip_attempt_{id}.mp4"
# fullmatch pattern — rejects traversal, wrong extension, empty id
_CLIP_RE = re.compile(r"clip_attempt_\d+\.mp4")


def saved_attempt_ids(root: Path) -> set[int]:
    """One walk of a save tree -> the attempt ids that have a saved clip.

    The FILENAME is the index (`attempt_<id>_...`, see ReplayService's class
    docstring), so nothing but the directory is needed to answer this. Bulk
    form of `find_saved` for `available_attempt_ids` — avoids a glob per
    attempt — and module-level so `main.py` can hand it to the tracker's
    startup prune, which must protect a saved clip's attempt from deletion
    (tracking/prune.py) whether or not replay is enabled this run.
    """
    if not root.exists():
        return set()
    ids: set[int] = set()
    for path in root.rglob("attempt_*.mp4"):
        match = re.match(r"attempt_(\d+)_", path.name)
        if match:
            ids.add(int(match.group(1)))
    return ids


# How many game frames LATER than its wall time a frame's picture appears in
# the footage. MEASURED 2026-08-22 from nine consecutive Forward-1 presses
# with Usamune's own frame counter and stick display on screen: lining the
# game's stick readings up against the track's by VALUE (the game never showed
# U67 L69 -- that was a frame the capture skipped -- and the track held it at
# frame 56) gave track frame = counter + 28 on every sample, while the
# wall-clock offset alone put the inspector at counter + 29: one frame ahead
# of the picture, constantly. The capture also jitters by one frame either
# way (his counter read 26, 27, 27, 29, 30, 31, 32, 33, 33) and does not
# drift; that part is in the footage and cannot be corrected from the clip.
DISPLAY_LAG_FRAMES = 1
# How much of a clip the feed log must account for before its bookkeeping is
# trusted over the pad reader's per-slot alignment (item 89). Measured on
# three clips: 100% and 92% on the two the reader only had to confirm, 73% on
# the one where it genuinely repaired capture damage. The gap between 73 and
# 92 is empty, so anything in 0.80-0.90 separates them; provisional on three
# clips from one session and worth re-measuring as they accumulate.
FEED_COVERAGE_MIN = 0.85


def _parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _open_explorer_select(path: Path) -> None:
    """Open File Explorer with the file pre-selected. explorer.exe wants the
    /select,"path" form as ONE argument string; it always exits 1, so this is
    fire-and-forget."""
    import subprocess
    subprocess.Popen(f'explorer /select,"{path}"')


def _slug(s: str) -> str:
    """Lower-case alphanumeric slug; apostrophes are removed (possessives stay
    joined), other non-alnum runs collapse to a single dash."""
    s = s.replace("'", "")  # "Whomp's" -> "Whomps" (don't insert a dash)
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")


def slug_filename(a, course: str, star: str) -> str:
    """Human-readable filename for a saved clip.

    IGT display format from format_igt is M'SS"CC (Usamune style).
    We replace ' -> m and " -> s so the filename is filesystem-safe:
    e.g. 0'11"43 -> 0m11s43. Segment attempts are RTA-only (spec
    2026-06-11): their time gets an explicit -rta marker.

    Parts list drops empty slugs so a segment attempt (empty star part)
    produces no double underscore.
    """
    if a.igt_frames is not None:
        igt = format_igt(a.igt_frames).replace("'", "m").replace('"', "s")
    elif a.rta_frames is not None:
        igt = format_igt(a.rta_frames).replace("'", "m").replace('"', "s") + "-rta"
    else:
        igt = "no-igt"
    suffix = "" if a.outcome == "success" else f"_{a.outcome}"
    parts = [p for p in (_slug(course), _slug(star)) if p]
    return f"attempt_{a.id:04d}_{'_'.join(parts)}_{igt}{suffix}.mp4"


class ReplayService:
    """Orchestrates replay operations for attempts.

    Public surface (consumed by Task 12 router):
      status()           -> dict
      view(attempt_id)   -> dict  {clip_url, duration_s, truncated, source, saved_path}
      save(attempt_id)   -> dict  {path, truncated}
      reveal(path)       -> None  (opens Explorer with the saved file selected)
      clip_path(name)    -> Path  (validated; raises LookupError on bad name)
      saved_clip_path(attempt_id) -> Path  (raises LookupError when not saved)
      find_saved(attempt_id)      -> Path | None
      lifecycle_start()
      lifecycle_stop()

    Saved replays are indexed by FILENAME, not by db row: slug_filename
    starts every saved clip with attempt_{id:04d}_, so a glob over
    save_root is the registry. That keeps the user free to reorganize or
    delete files in Explorer without anything going stale — the next
    lookup just sees the filesystem truth.
    """

    #: Measures a freshly cut clip's map against the game's OWN display and
    #: returns a `replay/mapalign.py::Alignment` (or None for no verdict).
    #: Injected by the composition root because it needs the input track,
    #: which this zone must not reach into. None = clips keep the map the
    #: timing constants produced, which is what every clip did until
    #: 2026-08-28 and what four rounds of live checks found wrong.
    map_aligner = None

    #: READS Usamune's input display out of the clip, glyph cell by glyph
    #: cell, and pins the map to what it read (`replay/padread.py::
    #: read_clip`), signature (clip, frame_map, attempt) -> PadReading |
    #: None. Injected like the aligner. When it answers, the ink anchor
    #: and the digit refit below are skipped -- the display has been read
    #: rather than weighed -- and the sidecar carries its verdict: how
    #: many slots the display confirmed and every one it contradicts.
    pad_reader = None

    #: Reads the on-screen CLOCK and joins it to the coherent
    #: (gGlobalTimer, usamune_overall) stamp carried by the matched picture
    #: ledger row.  Unlike pad_reader this is a frame-identity source, not an
    #: alignment search.  Clips captured before the stamp simply refuse and
    #: keep the existing path.
    timer_reader = None

    #: The input track's pads for an attempt, `{frame: (stick_x, stick_y,
    #: buttons)}`, for the capture-layer audit (`_audit_pad_stamps`): the
    #: pad the plugin copied for a picture must equal the track's pad at
    #: that frame. Injected from main.py, which owns the track.
    track_pads = None

    #: Holds the map to ONE answer per distinct picture in the footage
    #: (`replay/mapalign.py::quantised`). Injected like the aligner, and
    #: independent of it: a clip whose display cannot be read still gets
    #: this, because it needs nothing but the pictures.
    map_quantiser = None

    #: `mapalign.AnchorStats` (or None): the per-clip measured anchors,
    #: remembered so a clip whose digits cannot be read inherits the median
    #: of the clips whose digits could. Injected by the composition root
    #: with a real path under data/.
    anchor_stats = None

    #: Refits a clip's map picture-by-picture against its own digits
    #: (`replay/mapalign.py::digit_fitted`), signature
    #: (clip, frame_map, attempt) -> list | None. Injected like the aligner;
    #: None or a refusal leaves the anchored map exactly as it was.
    map_digit_fit = None

    #: Builds a frame map from the picture ledger's rows and the clip's own
    #: picture runs (`replay/mapalign.py::ledger_map`), signature
    #: (clip, rows, start_ts, duration_s, fps) -> list | None. Injected like
    #: the quantiser (it needs ffmpeg to decode the runs). None, or a ledger
    #: with no rows: the frame-clock series answer as before.
    ledger_mapper = None

    def __init__(self, cfg: ReplayConfig, recorder, extractor, tracker,
                 revealer=None, frame_clock=None):
        self.cfg = cfg
        self.recorder = recorder
        self.extractor = extractor
        self.tracker = tracker
        # WHEN each game frame happened (replay/frameclock.py), fed by the
        # poller. At extraction it becomes the sidecar's frame_map; None
        # (older wiring, tests) just means clips carry no map.
        self._frame_clock = frame_clock
        self._revealer = revealer or _open_explorer_select
        # One cut per attempt at a time. Two `view()` calls for the same
        # attempt used to run two ffmpeg processes over one output path --
        # his 100-coin replay came back black, its H.264 stream shredded,
        # after the LBLJ autodetect re-opened the drawer mid-extraction
        # (2026-09-02). The second caller waits, then finds the cached clip.
        self._cut_locks: dict[int, threading.Lock] = {}
        self._cut_locks_guard = threading.Lock()
        # clips_dir lives inside scratch_dir; it is created in lifecycle_start
        # AFTER recorder.start() so any future recursive wipe by the recorder
        # doesn't evict a directory we created first.
        self.clips_dir = cfg.scratch_dir / "clips"
        # Pads are settings-mutable (cfg is frozen): these live values are
        # what _span uses; update_settings replaces them.
        self.pre_pad_s = cfg.pre_pad_s
        self.post_pad_s = cfg.post_pad_s

    def _cut_lock(self, attempt_id: int) -> threading.Lock:
        with self._cut_locks_guard:
            return self._cut_locks.setdefault(attempt_id, threading.Lock())

    # -- queries -------------------------------------------------------------

    def status(self) -> dict:
        return {"enabled": True, **self.recorder.status()}

    def settings(self) -> dict:
        """Storage limits + where the bytes are. saved_bytes walks save_root
        on demand (panel-open frequency) — deliberately NOT part of the 5 s
        status poll."""
        root = self.cfg.save_root
        saved = (sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
                 if root.exists() else 0)
        return {"retention_s": self.recorder.ring.retention_s,
                "max_buffer_bytes": self.recorder.ring.max_bytes,
                "pre_pad_s": self.pre_pad_s,
                "post_pad_s": self.post_pad_s,
                "save_root": str(root),
                "saved_bytes": saved}

    def update_settings(self, retention_s: float | None,
                        max_buffer_bytes: int,
                        pre_pad_s: float | None = None,
                        post_pad_s: float | None = None) -> dict:
        """Validate -> persist -> apply live (ring evicts immediately; pads
        affect the next view(); the recorder's idle threshold follows the
        padding window). None pads = keep current. Persist before apply so
        a write failure can't leave limits applied but not durable."""
        pre = self.pre_pad_s if pre_pad_s is None else float(pre_pad_s)
        post = self.post_pad_s if post_pad_s is None else float(post_pad_s)
        validate_settings(retention_s, max_buffer_bytes, pre, post)
        save_settings(self.cfg.settings_path, retention_s, max_buffer_bytes,
                      pre, post)
        self.recorder.ring.set_limits(retention_s, max_buffer_bytes)
        self.pre_pad_s, self.post_pad_s = pre, post
        self.recorder.set_idle_after(pre + post)
        return self.settings()

    def _attempt(self, attempt_id: int):
        if self.tracker.db is None:
            raise RuntimeError("database unavailable")
        for a in self.tracker.db.attempts():
            if a.id == attempt_id:
                return a
        raise LookupError(f"no attempt {attempt_id}")

    def _span(self, a) -> tuple[datetime, datetime]:
        start = _parse_utc(a.started_utc) - timedelta(seconds=self.pre_pad_s)
        end = _parse_utc(a.ended_utc) + timedelta(seconds=self.post_pad_s)
        return start, end

    # -- commands ------------------------------------------------------------

    def find_saved(self, attempt_id: int) -> Path | None:
        """Locate an attempt's saved clip anywhere under save_root (the
        filename is the index — see class docstring). First match in
        sorted order wins if the user duplicated a file manually."""
        root = self.cfg.save_root
        if not root.exists():
            return None
        matches = sorted(root.rglob(f"attempt_{attempt_id:04d}_*.mp4"))
        return matches[0] if matches else None

    def saved_attempt_ids(self) -> set[int]:
        """This service's own save tree -> `saved_attempt_ids(root)`."""
        return saved_attempt_ids(self.cfg.save_root)

    def available_attempt_ids(self) -> list[int]:
        """Attempt ids whose replay is obtainable RIGHT NOW: a saved clip on
        disk, an already-extracted scratch clip, OR fully inside the live ring
        (extractable on demand — clicking runs the same view() extraction).

        The ring evicts old footage as it fills, so buffer coverage shifts over
        time; the Compare page recomputes this on every open so runs that have
        aged out of the buffer (and were never saved) drop off the picker
        instead of 409-ing 'no footage' on click. A run is buffer-covered only
        when the ring FULLY contains its span (start included) — a run whose
        beginning was already evicted would extract to a broken partial clip,
        so it counts as unavailable."""
        if self.tracker.db is None:
            return []
        saved_ids = self.saved_attempt_ids()
        cov = self.recorder.ring.coverage("video")
        buf_start, buf_end = cov if cov else (None, None)
        out: list[int] = []
        for a in self.tracker.db.attempts():
            if (a.id in saved_ids
                    or (self.clips_dir / _CLIP_NAME.format(id=a.id)).exists()):
                out.append(a.id)
                continue
            if (buf_start is not None and a.started_utc and a.ended_utc
                    and buf_start <= _parse_utc(a.started_utc)
                    and _parse_utc(a.ended_utc) <= buf_end):
                out.append(a.id)
        return out

    @staticmethod
    def _saved_meta(saved: Path) -> dict:
        """Sidecar metadata for a saved clip. Files saved before sidecars
        existed (pre 2026-06-12) have none: degrade to unknown duration and
        no truncation banner — the <video> element learns the real duration
        itself once loaded."""
        meta = saved.with_suffix(".json")
        if not meta.exists():
            return {}
        return json.loads(meta.read_text())

    def view(self, attempt_id: int) -> dict:
        """Clip metadata, cutting at most once per attempt at a time."""
        with self._cut_lock(attempt_id):
            return self._view(attempt_id)

    def _view(self, attempt_id: int) -> dict:
        """Return clip metadata, extracting and caching on first call.

        Source order: scratch cache -> saved file -> ring extraction.
        The scratch cache (clip + JSON sidecar both exist) dies with the
        buffer on restart — intentional; after that, a SAVED copy is the
        only source that can outlive the session, and only when neither
        exists do we cut from the ring. The saved fallback never shadows
        a viable extraction: scratch survives any session the ring covers.
        """
        a = self._attempt(attempt_id)
        name = _CLIP_NAME.format(id=attempt_id)
        clip = self.clips_dir / name
        meta = clip.with_suffix(".json")
        saved = self.find_saved(attempt_id)
        if clip.exists() and meta.exists():
            m = json.loads(meta.read_text())
            url, source = f"/api/replay/clips/{name}", "buffer"
            if "video_start_s" not in m:
                # A clip cut before 2026-09-01 carries no first pts in its
                # sidecar; measured once here rather than assumed 0, which is
                # the assumption that put every seek one picture early.
                m["video_start_s"] = video_start_of(
                    getattr(self.extractor, "ffmpeg", None), clip)
                meta.write_text(json.dumps(m))
        elif saved is not None:
            m = self._saved_meta(saved)
            url, source = f"/api/replay/saved/{attempt_id}", "saved"
        else:
            start, end = self._span(a)
            self._wait_for_tail(end)
            res = self.extractor.extract(self.recorder.ring, start, end, clip)
            m = {"duration_s": res.duration_s, "truncated": res.truncated}
            # The clip's own first video timestamp: a cut leaves its
            # sub-frame remainder on the first picture (5782: 0.011 s), and
            # every seek and every panel read must count from it or they
            # land one picture early (chain-input-timeline-frame, hop 6).
            m["video_start_s"] = res.video_start_s
            if res.frame_times is not None:
                # The picture feed (item 38): the clip is VFR, one frame
                # per captured picture, and every consumer counts slots
                # through these times, never as k / fps.
                m["frame_times"] = [round(t, 6) for t in res.frame_times]
                m["encode"] = "picture_feed"
            if res.start_utc is not None:
                m["start_utc"] = res.start_utc.isoformat()
                if res.duration_s:
                    # WHICH game frame each video frame shows (round 32 item
                    # 17): built here, at the one moment the clip's span and
                    # capture's memory overlap, and stored in the sidecar so
                    # a saved copy keeps it after capture forgets. The
                    # picture ledger answers first (item 40: capture's own
                    # per-picture record); the frame-clock series are the
                    # fallback for clips from before it, and the display lag
                    # rides INSIDE the map either way; anchor_offset_s stays
                    # the fallback for a clip that has none.
                    if res.frame_times is not None:
                        # One frame per picture: the feed log SAYS which
                        # row each frame is; nothing is inferred from
                        # runs, and the CFR series cannot describe it.
                        self._map_from_feeds(m, res)
                        if m.get("frame_map") is None:
                            # A loaded machine drops pictures at the sink's
                            # queue and writes the rest late, so the log can
                            # stop covering the clip: his Haunted Books run
                            # captured 680 pictures, encoded 387, and matched
                            # 178 -- and shipped with NO map, which the panel
                            # answers by falling back to plain arithmetic
                            # ("lots of incorrect frames", 2026-09-02). The
                            # ledger path matches rows to the clip's own
                            # picture runs and does not care how many frames
                            # went missing, so it is the fallback, not none.
                            log.warning("feed log did not cover the clip; "
                                        "falling back to the picture ledger")
                            self._map_from_ledger(m, clip, res)
                    else:
                        self._map_from_ledger(m, clip, res)
                    if (m.get("frame_map") is None
                            and res.frame_times is None
                            and self._frame_clock is not None):
                        mapped = self._frame_clock.frame_map(
                            res.start_utc, res.duration_s, self.cfg.fps,
                            DISPLAY_LAG_FRAMES / GAME_FPS)
                        if mapped is not None:
                            # Which series answered rides beside the map so
                            # the pixel scorer's verdict names what it
                            # scored ("ledger" = the picture ledger,
                            # "presents" = v4, "feeds" = v2, "edges" = v1).
                            m["frame_map"], m["frame_map_source"] = mapped
                    if m.get("frame_map") is not None:
                        if self._rows_are_exact(m):
                            self._take_the_stamps(m, clip, a)
                        else:
                            self._align_to_the_footage(m, clip, a)
            meta.write_text(json.dumps(m))
            url, source = f"/api/replay/clips/{name}", "buffer"
        # fps = encoded rate (CFR); game_fps = SM64 logic rate — the
        # frame-step UI steps in GAME frames: each spans two encoded
        # frames, so stepping 1/fps changed the image only every 2nd press
        # (live-reported 2026-06-12). Saved sidecars stamp fps at save
        # time so old clips step correctly even if the config changes.
        return {"clip_url": url,
                "duration_s": m.get("duration_s"),
                "truncated": m.get("truncated", False),
                "fps": m.get("fps", self.cfg.fps), "game_fps": GAME_FPS,
                "source": source,
                **self._coverage_notices(a, m),
                "anchor_offset_s": self._anchor_offset(a, m),
                "frame_map": m.get("frame_map"),
                "frame_map_source": m.get("frame_map_source"),
                # The pad reader's verdict (sure / agree / nowhere /
                # disagreements): how many pictures the game's own display
                # confirmed the map on. None for a clip it could not read.
                "pad_reading": m.get("pad_reading"),
                # The CLOCK/RAM join: `mechanical` slots were named directly;
                # `bridged` slots had no usable clock pair and retain shifted
                # bookkeeping. None for clips captured before the IGT stamp.
                "timer_reading": m.get("timer_reading"),
                # A capture-layer clip's own check: the pad the plugin copied
                # for each picture against the input track at that frame.
                "pad_stamp_agreement": m.get("pad_stamp_agreement"),
                "video_start_s": m.get("video_start_s", 0.0),
                # Per-frame timestamps of a picture-feed clip (VFR); None
                # for a CFR clip, whose slots are k / fps from
                # video_start_s.
                "frame_times": m.get("frame_times"),
                "encode": m.get("encode", "cfr"),
                "feed_match": m.get("feed_match"),
                # WHICH map shipped and whether the capture was healthy
                # (item 89): "feed_log+offset" is the recorder's own
                # bookkeeping moved by one whole-clip integer, which is what a
                # well-covered clip gets; "reader_aligned" means the feed log
                # did not cover the clip and the display had to repair it
                # per slot, so the timeline says so rather than looking clean.
                "frame_map_mode": m.get("frame_map_mode"),
                "frame_map_degraded": m.get("frame_map_degraded", False),
                # Distinct from capture degradation: True means the CLOCK
                # named part of the map mechanically and recorder bookkeeping
                # bridged slots where the clock was frozen/unreadable.
                "frame_map_inferred": m.get("frame_map_inferred", False),
                "feed_coverage": m.get("feed_coverage"),
                "saved_path": str(saved) if saved is not None else None}

    # A frame and a half of slack: the clip's own first-frame stamp and the
    # attempt's anchor are read off two clocks, so a hair of disagreement is
    # normal and must not raise an alarm.
    _COVERAGE_SLACK_S = 0.05

    def _coverage_notices(self, a, meta: dict) -> dict:
        """Whether the clip is missing any of the ATTEMPT, told apart from
        the ordinary case of a short lead-in.

        `truncated` only says the ring could not serve the whole PADDED
        span, which is usually just less than the three seconds of run-up --
        the attempt itself entirely present. Drawing "Starts mid-attempt"
        off that flag put a warning on an ordinary reset and read as a
        defect: "but... I just reset as normal? What does this even mean?
        This seems wrong to me" (2026-08-28). The honest question is
        whether the clip starts after the attempt did, and the timestamps
        answer it outright.
        """
        start = meta.get("start_utc")
        duration = meta.get("duration_s")
        if start is None or duration is None:
            # A sidecar from before start_utc existed cannot be asked; the
            # coarse flag is all there is, and it is what shipped.
            return {"starts_mid_attempt": bool(meta.get("truncated")),
                    "ends_early": False}
        clip_start = _parse_utc(start)
        clip_end = clip_start + timedelta(seconds=duration)
        began = _parse_utc(a.started_utc)
        ended = _parse_utc(a.ended_utc) if a.ended_utc else None
        return {
            "starts_mid_attempt":
                (clip_start - began).total_seconds() > self._COVERAGE_SLACK_S,
            "ends_early": (ended is not None and (ended - clip_end)
                           .total_seconds() > self._COVERAGE_SLACK_S),
        }

    def _map_from_ledger(self, meta: dict, clip: Path, res) -> None:
        """The frame map from capture's own per-picture record (item 40).

        His spec: "when we build the video, at every single frame of
        gameplay, we have access to all the memory addresses and data
        in-game that would allow us to embed each frame with extra
        information that we can use to do any type of future analysis
        with." The recorder's picture ledger stamped every distinct
        picture at capture; here the clip's slice of those rows becomes
        the sidecar's `picture_ledger` (the durable per-frame record,
        extra stamps included) and, matched to the clip's picture runs,
        the frame map itself -- already one answer per picture, so the
        quantiser has nothing left to move. Any failure leaves the meta
        untouched and the series path answers as before."""
        ledger = getattr(self.recorder, "ledger", None)
        if ledger is None or self.ledger_mapper is None:
            return
        start = res.start_utc.timestamp()
        try:
            rows = ledger.rows_between(start - 0.5,
                                       start + res.duration_s + 0.5)
            if not rows:
                return
            built = self.ledger_mapper(clip, rows, start, res.duration_s,
                                       self.cfg.fps,
                                       frame_times=res.frame_times)
        except Exception:
            log.exception("picture-ledger mapping failed; the frame-clock "
                          "series answer instead")
            return
        meta["picture_ledger"] = [
            {**row, "ts": round(row["ts"] - start, 4)} for row in rows]
        if built is None:
            return
        meta["frame_map"] = built
        meta["frame_map_source"] = "ledger"
        meta["frame_map_quantised"] = True

    def _map_from_feeds(self, meta: dict, res) -> None:
        """The frame map READ off the picture feed's log (item 38).

        Frame k of the clip sits at start_utc + frame_times[k]; the
        ledger's feed log names the row the sink wrote at that moment,
        and the row's RAM stamp names the game frame. The rows ride the
        sidecar as `picture_ledger` and the match statistics as
        `feed_match`, so a verdict is answerable from the file. Any
        failure leaves the meta without a map (the CFR series cannot
        describe a VFR clip, so nothing else answers)."""
        ledger = getattr(self.recorder, "ledger", None)
        if ledger is None or not hasattr(ledger, "feeds_between"):
            return
        start = res.start_utc.timestamp()
        end = start + res.duration_s
        try:
            rows = ledger.rows_between(start - 1.5, end + 1.0)
            feeds = ledger.feeds_between(start - 1.0, end + 1.0)
            if not rows or not feeds:
                return
            built, repeats, stats = feed_map(
                res.frame_times, start, rows, feeds, DISPLAY_LAG_FRAMES)
            # The SAME feed-to-row join, projected onto the pair sampled
            # inside InputSampler's counter sandwich.
            clock_pairs, _clock_repeats, _clock_stats = feed_map(
                res.frame_times, start, rows, feeds,
                row_value=lambda row: (
                    (row["frame"], row["igt_overall"])
                    if row.get("frame") is not None
                    and row.get("igt_overall") is not None else None))
        except Exception:
            log.exception("feed-log mapping failed; the clip carries no map")
            return
        meta["picture_ledger"] = [
            {**row, "ts": round(row["ts"] - start, 4)} for row in rows]
        meta["feed_match"] = stats
        if built is None:
            log.warning("feed log covers too little of the clip: %s", stats)
            return
        meta["frame_map"] = built
        meta["repeats"] = repeats
        meta["frame_map_source"] = "feed_log"
        # Kept only until the timer reader runs; the durable evidence is
        # already in picture_ledger.
        if clock_pairs is not None:
            meta["_clock_pairs"] = clock_pairs
        # NOT marked quantised: one frame is one CAPTURED picture, but the
        # emulator re-presents a render when the game lags, and those two
        # grabs land as two frames with advancing RAM stamps -- the same
        # picture twice (115 of 775 on his pyramid clip, 2026-09-02). The
        # quantiser's pixel runs hold both to one answer, so a display-off
        # clip is honest too, and the reader's own pixel flag does the rest.
        log.info("frame map read off the feed log: %s", stats)

    def _hold_one_answer_per_picture(self, meta: dict, clip: Path) -> None:
        """One timeline frame per PICTURE, however many video frames it
        occupies.

        A 30 fps game captured at 60 gives two video frames per picture,
        and the capture's jitter makes it one or three often enough to
        see -- measured on his clip 4374: 397 runs of two against 11 of
        one and 13 longer. A map built from CLOCKS crosses those runs
        wherever its boundaries fall half a frame off the pictures', so
        stepping forward showed the same picture with a different pad
        beside it. His ruling: "if there's duplicated frames, input
        timeline should be identical for the sequential duplicated
        frames." The runs supply the boundaries; the map still supplies
        the advance, because a count-based map would drift by however
        many game frames the capture missed.
        """
        if self.map_quantiser is None or meta.get("frame_map_quantised"):
            return                     # a ledger-built map already holds it
        try:
            held = self.map_quantiser(clip, meta["frame_map"])
        except Exception:
            log.exception("picture-run quantising failed; map left as built")
            return
        if held is None:
            return
        moved = sum(1 for was, now in zip(meta["frame_map"], held)
                    if was != now)
        meta["frame_map"] = held
        meta["frame_map_quantised"] = True
        log.info("frame map held to one answer per picture: %d slots moved",
                 moved)

    #: the share of a capture-layer clip's rows that must be exact for the
    #: rows to be the map; a row whose present saw zero or two display lists
    #: is marked inexact by the plugin source, and a few such rows (a lag
    #: frame) do not hand the whole clip back to inference
    PLUGIN_EXACT_SHARE = 0.98

    @classmethod
    def _rows_are_exact(cls, meta: dict) -> bool:
        """A clip recorded through the capture layer (item 95): its ledger
        rows carry `exact` -- the frame the plugin read inside Project64 at
        the one display list that drew that picture -- on all but a handful."""
        rows = meta.get("picture_ledger") or []
        stamped = [row for row in rows if "exact" in row]
        if not rows or len(stamped) != len(rows):
            return False
        exact = sum(1 for row in stamped if row["exact"])
        return exact >= cls.PLUGIN_EXACT_SHARE * len(rows)

    def _take_the_stamps(self, meta: dict, clip: Path, attempt) -> None:
        """The map IS the rows: nothing is aligned, joined or read into
        identity. The footage aligner, the timer join and the pad reader's
        alignment stand down; the pad reader still AUDITS (his 100% test,
        3 s a clip) and the stamp's own pad is checked against the input
        track -- a per-picture agreement with no pixels in it."""
        rows = meta.get("picture_ledger") or []
        inexact = sum(1 for row in rows if not row.get("exact"))
        meta["frame_map_base_source"] = meta.get("frame_map_source")
        meta["frame_map_source"] = "plugin"
        meta["frame_map_mode"] = "plugin"
        meta["frame_map_inferred"] = inexact > 0
        meta["plugin_inexact_rows"] = inexact
        meta.pop("_clock_pairs", None)
        self._read_the_display(meta, clip, attempt, audit_only=True)
        self._audit_pad_stamps(meta, attempt)

    def _audit_pad_stamps(self, meta: dict, attempt) -> None:
        """Every exact row's copied pad against the input track's pad at
        that frame: `pad_stamp_agreement` = {pictures, agree, disagreements}.
        Absent when no track lookup is wired or the track is empty."""
        if self.track_pads is None:
            return
        try:
            pads = self.track_pads(attempt)
        except Exception:
            log.exception("track lookup failed; no pad-stamp audit")
            return
        if not pads:
            return
        pictures = agree = 0
        disagreements = []
        for slot, row in enumerate(meta.get("picture_ledger") or []):
            stamped = row.get("pad")
            frame = row.get("frame")
            if stamped is None or frame is None or frame not in pads:
                continue
            pictures += 1
            tracked = list(pads[frame])
            if tracked == list(stamped):
                agree += 1
            elif len(disagreements) < 50:
                disagreements.append([slot, frame, tracked, list(stamped)])
        meta["pad_stamp_agreement"] = {"pictures": pictures, "agree": agree,
                                       "disagreements": disagreements}
        if pictures and agree != pictures:
            log.warning("pad stamps disagree with the track on %d of %d pictures",
                        pictures - agree, pictures)

    def _align_to_the_footage(self, meta: dict, clip: Path, attempt) -> None:
        """Shift the fresh map onto what the clip's own pixels show.

        The timing constants upstream estimate a journey (game logic ->
        plugin present -> capture -> encoded slot) whose length is not
        ours to know; the clip knows it, because Usamune draws the pad
        into every frame. So this measures rather than assumes, and
        records BOTH numbers in the sidecar -- the offset applied and the
        strength of the evidence -- so a later verdict is answerable from
        the file instead of by eye. No aligner, no display in the footage,
        or no clear winner: the map stands as built and says so.
        """
        self._hold_one_answer_per_picture(meta, clip)
        if self._read_the_timer(meta, clip, attempt):
            # The timer owns identity; the pad reader is now only an auditor
            # and can never move the map it is being asked to check.
            self._read_the_display(meta, clip, attempt, audit_only=True)
            meta.pop("frame_map_quantised", None)
            self._hold_one_answer_per_picture(meta, clip)
            meta.pop("_clock_pairs", None)
            return
        if self._read_the_display(meta, clip, attempt):
            # THE PIXELS OWN THE BOUNDARIES, the reader owns the VALUES. The
            # reader may still advance the map across a picture the emulator
            # merely re-presented, and then stepping forward lands on the same
            # image twice with a different pad beside it -- his standing rule:
            # "when the user plays back their video, they NEVER see a duplicate
            # frame" (2026-09-02). Re-holding the read map to the picture runs
            # makes one answer per picture an invariant of what SHIPS, so the
            # stepper (which walks to the next distinct map value) cannot land
            # on a held picture at all.
            meta.pop("frame_map_quantised", None)
            self._hold_one_answer_per_picture(meta, clip)
            meta.pop("_clock_pairs", None)
            return
        meta.pop("_clock_pairs", None)
        if self.map_aligner is None:
            return
        try:
            found = self.map_aligner(clip, meta["frame_map"], attempt)
        except Exception:
            log.exception("frame-map alignment failed; keeping the built map")
            return
        # The aligner may answer (global, windows): the pipeline lag drifts
        # by whole frames WITHIN a clip (attempt 4518's three shelves), so
        # each window of footage names its own offset where its digits can.
        found, windows = (found if isinstance(found, tuple)
                          else (found, []))
        if found is None:
            # Digits unreadable in THIS clip: inherit the anchor other clips
            # measured (the stats store's median) rather than go uncorrected.
            learned = (self.anchor_stats.fallback_offset()
                       if self.anchor_stats is not None else None)
            if learned:
                meta["frame_map"] = mapalign.frame_corrected(
                    meta["frame_map"], learned)
                meta["frame_map_offset"] = learned
                meta["frame_map_learned"] = True
                log.info("frame map corrected by the LEARNED anchor: "
                         "%+d slots (digits unreadable here)", learned)
            meta["frame_map_aligned"] = False
            return
        # The correction is applied in the FRAME domain: an odd slot shift
        # would split pictures that quantising just unified. With windows,
        # each picture takes its nearest confident window's offset --
        # piecewise, which is what a stepped lag needs; without, the global
        # offset applies to everything as before.
        if windows:
            meta["frame_map"] = mapalign.window_corrected(meta["frame_map"],
                                                          windows)
            meta["frame_map_windows"] = [list(row) for row in windows]
        else:
            meta["frame_map"] = mapalign.frame_corrected(meta["frame_map"],
                                                         found.offset)
        meta["frame_map_aligned"] = True
        meta["frame_map_offset"] = found.offset
        meta["frame_map_fit"] = round(found.fit, 4)
        self._refit_to_the_digits(meta, clip, attempt)
        if self.anchor_stats is not None:
            self.anchor_stats.record(getattr(attempt, "id", 0),
                                     found.offset, found.fit)
        log.info("frame map aligned to the footage: %+d slots "
                 "(fit %.3f, margin %.3f, %d slots paired)",
                 found.offset, found.fit, found.margin, found.paired)

    def _read_the_timer(self, meta: dict, clip: Path, attempt) -> bool:
        """Replace the map with the direct CLOCK/RAM join when it covers.

        No controller data reaches this callback.  The old map participates
        only after the join, as an explicitly counted bridge for unreadable,
        frozen, or unpaired slots.
        """
        pairs = meta.get("_clock_pairs")
        if self.timer_reader is None or pairs is None:
            return False
        try:
            mapping = self.timer_reader(clip, meta["frame_map"], pairs)
        except Exception:
            log.exception("timer reader failed; keeping the prior map")
            return False
        if mapping is None:
            return False
        meta["frame_map_base_source"] = meta.get("frame_map_source")
        meta["frame_map"] = list(mapping.frame_map)
        meta["frame_map_source"] = "timer"
        meta["frame_map_mode"] = ("timer+bridge" if mapping.bridged
                                  else "timer")
        meta["frame_map_inferred"] = mapping.bridged > 0
        meta["timer_reading"] = mapping.as_dict()
        log.info("frame map joined through the CLOCK: %d mechanical, %d "
                 "bridged, %d rejected", mapping.mechanical,
                 mapping.bridged, mapping.rejected)
        return True

    def _read_the_display(self, meta: dict, clip: Path, attempt,
                          audit_only: bool = False) -> bool:
        """Pin the map to the pad Usamune drew into every picture.

        The pad reader (round 32, 2026-09-01) reads the display's six
        glyph cells per video frame and aligns the track to them, so the
        map is RIGHT wherever the display can be checked and no worse
        than the clocks' answer where it cannot. True when it answered:
        the map is replaced, and `pad_reading` in the sidecar says how
        many slots the display confirmed, how many it contradicts, and
        which. A refusal (display off, too little read) or any failure
        leaves the built map for the ink anchor, as before.
        """
        if self.pad_reader is None:
            return False
        try:
            # A picture-feed clip knows which frames are heartbeat repeats
            # (the feed log said so); the reader takes them as its picture
            # flags instead of guessing them from pixels. A CFR clip has
            # none, and the hook keeps its three-argument shape for it.
            repeats = meta.get("repeats")
            reading = (self.pad_reader(clip, meta["frame_map"], attempt,
                                       repeats=repeats)
                       if repeats is not None
                       else self.pad_reader(clip, meta["frame_map"], attempt))
        except Exception:
            log.exception("pad reader failed; keeping the built map")
            return False
        if reading is None:
            meta["frame_map_read"] = False
            return False
        verdict = reading.verdict
        if audit_only:
            # PadReading.audit is scored against the map the caller handed
            # in, before the reader aligns anything.  Older injected readers
            # have no such field; their aligned verdict is still useful as a
            # diagnostic but is never allowed to mutate timer identity.
            verdict = getattr(reading, "audit", None) or verdict
            meta["frame_map_read"] = True
            meta["pad_reading"] = verdict.as_dict()
            log.info("timer map audited by the pad display: %d of %d slots "
                     "agree, %d contradicted", verdict.agree, verdict.sure,
                     verdict.sure - verdict.agree)
            return True
        # THE READER IS AN AUDITOR ON A WELL-COVERED CLIP (item 89). A fresh
        # review measured what its per-slot DP actually does: 50-77% of a
        # clip's frames repeat their predecessor's pad, so the display cannot
        # tell them apart, and 78-94% of the slots the DP MOVES sit between two
        # such frames -- unfalsifiable moves, which is what one wrong button
        # frame looks like (his Elevator Tour frame 13: R drawn where the
        # screen showed Cdown, in a neutral stretch). On the two clips whose
        # feed log covered them, the bookkeeping plus ONE flat integer matched
        # the best per-window correction chosen with HINDSIGHT and came within
        # 0.3 and 0.5 points of the DP. So the DP's freedom buys 2 slots of 676
        # and costs run-to-run drift wherever the screen is silent.
        #
        # Coverage decides. Below the cutoff the DP genuinely earns its keep
        # (on a 73%-covered clip: 85.6% -> 95.6%, where even a hindsight
        # per-window corrector reached only 90.4%) -- but it is then patching
        # capture damage, and the clip says so.
        book = list(meta["frame_map"])          # what the feed log worked out
        match = meta.get("feed_match") or {}
        frames = match.get("frames") or 0
        coverage = (match.get("matched", 0) / frames) if frames else 0.0
        trusted = (coverage >= FEED_COVERAGE_MIN
                   and verdict.offset_margin >= padread.OFFSET_MARGIN_MIN)
        if trusted:
            meta["frame_map"] = mapalign.frame_corrected(book, verdict.offset)
            meta["frame_map_mode"] = "feed_log+offset"
        else:
            meta["frame_map"] = list(reading.frame_map)
            meta["frame_map_mode"] = "reader_aligned"
        meta["frame_map_degraded"] = not trusted
        meta["feed_coverage"] = round(coverage, 4)
        meta["frame_map_read"] = True
        meta["pad_reading"] = verdict.as_dict()
        log.info("frame map READ off the display: %d of %d slots confirmed "
                 "(%.2f%%), %d contradicted, %d matched nothing nearby",
                 verdict.agree, verdict.sure, 100 * verdict.agreement,
                 verdict.sure - verdict.agree, verdict.nowhere)
        return True

    def _refit_to_the_digits(self, meta: dict, clip: Path, attempt) -> None:
        """Give every picture its OWN frame (round 32 item 55).

        The anchors above move the whole map, or a stretch of it, by one
        number. What his BBH clip actually does is drift where the capture
        dropped frames -- exact at 87-89, one to two early at 129-141 -- so
        the last step assigns each picture the frame whose digits explain
        its ink, monotonically. Any failure leaves the anchored map.
        """
        if self.map_digit_fit is None:
            return
        try:
            fitted = self.map_digit_fit(clip, meta["frame_map"], attempt)
        except Exception:
            log.exception("digit refit failed; keeping the anchored map")
            return
        if fitted is None:
            return
        moved = sum(1 for was, now in zip(meta["frame_map"], fitted)
                    if was != now)
        meta["frame_map"] = fitted
        meta["frame_map_digitfit"] = True
        log.info("frame map refitted to the digits: %d slots moved", moved)

    def _anchor_offset(self, a, meta: dict) -> float:
        """Where in the clip the anchor frame's PICTURE is on screen, in
        seconds -- what a consumer drawing the input track over the footage
        shifts by.

        Two parts. The wall-clock part: the clip is cut `pre_pad_s` BEFORE
        the anchor while the input track starts AT it, so without the shift
        every input lands three seconds early (live report 2026-08-22: "the
        input reader shows a totally different angle and shows me pressing
        A/B"). Measured from the clip's own recorded first frame where the
        sidecar has it; a sidecar written before that field existed falls
        back to the pad setting, which is exact unless the ring had evicted
        the lead-in.

        The display-lag part, `DISPLAY_LAG_FRAMES`: the screen shows the
        frame the game finished a frame ago, so the picture of frame N sits
        one frame later in the footage than N's wall time. See the constant.

        A clip whose sidecar carries a `frame_map` does not need this number
        -- the map says outright which game frame each video frame shows,
        jitter included -- so consumers use the map first and this offset is
        the fallback for clips cut before the frame clock existed.
        """
        start = meta.get("start_utc")
        if start is None:
            wall = float(self.pre_pad_s)
        else:
            wall = max(0.0, (_parse_utc(a.started_utc)
                             - _parse_utc(start)).total_seconds())
        return wall + DISPLAY_LAG_FRAMES / GAME_FPS

    def _wait_for_tail(self, end_utc: datetime) -> None:
        """Bounded wait: a click right after the event can outrace the last
        segment's rotation (spec: post-padding race)."""
        deadline = time.monotonic() + self.cfg.extract_wait_s
        while time.monotonic() < deadline:
            if not self.recorder.status().get("recording"):
                return
            cov = self.recorder.ring.coverage("video")
            if cov is not None and cov[1] >= end_utc:
                return
            time.sleep(0.25)

    def save(self, attempt_id: int) -> dict:
        """Persist a clip to the permanent save tree (date/session/).

        Idempotent: an attempt that already has a saved file returns it
        as-is (re-saving with different pads = delete the file in Explorer
        first). Otherwise view() extracts the clip (cached when already
        cut) and we copy it out with a metadata sidecar — the sidecar is
        what makes the clip self-describing in later sessions, after the
        scratch cache and ring are gone.
        """
        a = self._attempt(attempt_id)
        existing = self.find_saved(attempt_id)
        if existing is not None:
            m = self._saved_meta(existing)
            return {"path": str(existing), "truncated": m.get("truncated", False)}
        self.view(attempt_id)  # ensure clip exists (cached when already cut)
        clip = self.clips_dir / _CLIP_NAME.format(id=attempt_id)
        ended_local = _parse_utc(a.ended_utc).astimezone()  # folder by local date
        dest_dir = (self.cfg.save_root / ended_local.strftime("%Y-%m-%d")
                    / f"session_{a.session_id}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        if a.segment_id is not None:
            c_name = next((d.name for d in self.tracker.segment_defs
                           if d.id == a.segment_id),
                          f"segment-{a.segment_id}")
            s_name = ""
        else:
            c_name = course_name(a.course_id) if a.course_id is not None else "no-course"
            s_name = (star_name(a.course_id, a.star_id)
                      if a.star_id is not None and a.course_id is not None else "no-star")
        dest = dest_dir / slug_filename(a, c_name, s_name)
        shutil.copy2(clip, dest)
        m = json.loads(clip.with_suffix(".json").read_text())
        # fps stamped at save time: the step buttons must match the clip's
        # actual encode rate even if cfg.fps changes in a future version.
        dest.with_suffix(".json").write_text(
            json.dumps({**m, "fps": self.cfg.fps}))
        return {"path": str(dest), "truncated": m["truncated"]}

    def reveal(self, path_str: str) -> None:
        """Open Explorer with a SAVED clip selected. Only paths inside
        save_root are allowed — the path comes back from our own save()
        response, but the endpoint is reachable by anything on localhost."""
        root = self.cfg.save_root.resolve()
        p = Path(path_str).resolve()
        if not p.is_relative_to(root) or not p.is_file():
            raise LookupError("no such saved replay")
        self._revealer(p)

    def clip_path(self, name: str) -> Path:
        """Return validated Path for serving a clip.

        fullmatch rejects directory traversal and anything that isn't
        exactly one of our clip names (e.g. wrong extension, empty id).
        """
        if not _CLIP_RE.fullmatch(name):
            raise LookupError("no such clip")
        p = self.clips_dir / name
        if not p.exists():
            raise LookupError("no such clip")
        return p

    def saved_clip_path(self, attempt_id: int) -> Path:
        """Saved-clip path for serving. The id is the only input (an int
        path param) — no name validation needed; the glob can only ever
        land inside save_root."""
        p = self.find_saved(attempt_id)
        if p is None:
            raise LookupError("no saved replay for this attempt")
        return p

    # -- lifecycle (called from app lifespan) --------------------------------

    def lifecycle_start(self) -> None:
        # Start recorder first; it may wipe scratch_dir contents on init.
        # clips_dir is created after so a future recursive wipe doesn't
        # evict a directory we made first.
        self.recorder.start()
        self.clips_dir.mkdir(parents=True, exist_ok=True)

    def lifecycle_stop(self) -> None:
        self.recorder.stop()
