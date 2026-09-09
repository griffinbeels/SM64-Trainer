"""Attempt -> span -> clip -> save.

Error taxonomy matches server/api.py:
  LookupError  -> 404 (no such attempt/clip)
  ValueError   -> 409 (no footage / span too short)
  RuntimeError -> 503 (db unavailable)
Anything else (e.g. codec failure on a corrupt segment) is a genuine 500 —
extract.py already guarantees no partial file survives those.
"""
import asyncio
from contextlib import contextmanager, nullcontext
from sm64_events.replay.sessiongate import SessionGate
import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from sm64_events.core.timefmt import GAME_FPS, format_igt
from sm64_events.core.profiling import measured
from sm64_events.memory.addresses import course_name, star_name
from sm64_events.replay.association import association_problem, valid_picture_times
from sm64_events.replay.feedmap import feed_map
from sm64_events.replay.navigation import captured_input_span, attempt_start_slot
from sm64_events.replay.reviewstate import ReviewStateStore
from sm64_events.replay.publication import publish as publish_saved, recover as recover_saved
from sm64_events.replay.extract import frame_times_of, video_start_of
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
# A source-linked picture keeps its captured state. The former one-frame
# subtraction was inferred through the retired fitted timestamp join. Fresh
# source-PTS clips 7718/7685/7739 visibly match their own capture row (timer,
# stick and A edge); selecting the preceding row reintroduced the desync.
# Legacy maps without that source link remain unverified, not reinterpreted.


def _parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _picture_states(meta: dict) -> list | None:
    """Project validated capture references; never resolve a repeated counter."""
    slots = meta.get("state_rows")
    if slots is None:
        return None
    rows = meta.get("picture_ledger") or []
    states = []
    for index in slots:
        row = rows[index] if index is not None else {}
        pad, mario = row.get("pad"), row.get("mario")
        if (not isinstance(pad, (list, tuple)) or len(pad) != 3
                or not all(type(value) is int for value in pad)):
            states.append(None)
            continue
        yaw = (mario[1] if isinstance(mario, (list, tuple)) and len(mario) == 3
               and type(mario[1]) is int else None)
        states.append({"stick_x": pad[0], "stick_y": pad[1],
                       "buttons": pad[2], "yaw": yaw})
    return states


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

    #: The input track's pads for an attempt, `{frame: (stick_x, stick_y,
    #: buttons)}`, for the capture-layer audit (`_audit_pad_stamps`): the
    #: pad the plugin copied for a picture must equal the track's pad at
    #: that frame. Injected from main.py, which owns the track. This is the
    #: ONE injected hook left; there were seven, one per generation of the
    #: derived frame map, and the capture layer retired all of them.
    track_pads = None

    def __init__(self, cfg: ReplayConfig, recorder, extractor, tracker,
                 revealer=None):
        self.cfg = cfg
        self.recorder = recorder
        self.extractor = extractor
        self.tracker = tracker
        self._revealer = revealer or _open_explorer_select
        # One cut per attempt at a time. Two `view()` calls for the same
        # attempt used to run two ffmpeg processes over one output path --
        # his 100-coin replay came back black, its H.264 stream shredded,
        # after the LBLJ autodetect re-opened the drawer mid-extraction
        # (2026-09-02). The second caller waits, then finds the cached clip.
        self._cut_locks: dict[int, threading.Lock] = {}
        self._cut_locks_guard = threading.Lock()
        self._review_state = ReviewStateStore()
        # clips_dir lives inside scratch_dir; it is created in lifecycle_start
        # AFTER recorder.start() so any future recursive wipe by the recorder
        # doesn't evict a directory we created first.
        self.clips_dir = cfg.scratch_dir / "clips"
        # Pads are settings-mutable (cfg is frozen): these live values are
        # what _span uses; update_settings replaces them.
        self.pre_pad_s = cfg.pre_pad_s
        self.post_pad_s = cfg.post_pad_s
        self._save_guard = threading.Lock()
        self._active_saves = 0
        self._session_gate = SessionGate()
        self._save_failures: dict[int, str] = {}
        self._recovery_failures: list[str] = []
        self.recorder.scratch_protection = self._protected_scratch

    def _protected_scratch(self):
        with self._save_guard:
            return [self.cfg.scratch_dir] if self._active_saves else []

    def _pin_clip(self, attempt_id: int):
        pin = getattr(self.recorder.ring, "pin_temp", None)
        return pin(f"attempt:{attempt_id}") if pin else nullcontext()

    def _pin_span(self, start: datetime, end: datetime):
        pin = getattr(self.recorder.ring, "pin", None)
        return pin("video", start, end) if pin else nullcontext()

    def _register_clip(self, attempt, clip: Path) -> None:
        register = getattr(self.recorder.ring, "register_temp", None)
        if register is not None:
            start, end = self._span(attempt)
            register(f"attempt:{attempt.id}", [clip, clip.with_suffix(".json")], start, end)

    async def preserve_pb(self, attempt_id: int) -> dict:
        """Await media preservation without occupying the game's event loop."""
        try:
            result = await asyncio.to_thread(self.save, attempt_id)
            return {"status": "saved", **result}
        except Exception as error:
            message = str(error)
            with self._save_guard:
                self._save_failures[attempt_id] = message
                # Diagnostic state is bounded; durable publication intents are separate.
                if len(self._save_failures) > 32:
                    self._save_failures.pop(next(iter(self._save_failures)))
            log.exception("PB time saved, but replay %s could not be preserved", attempt_id)
            return {"status": "failed", "message": message}

    def _cut_lock(self, attempt_id: int) -> threading.Lock:
        with self._cut_locks_guard:
            return self._cut_locks.setdefault(attempt_id, threading.Lock())

    # -- queries -------------------------------------------------------------

    def status(self) -> dict:
        with self._save_guard:
            failures = dict(self._save_failures)
        return {"enabled": True, **self.recorder.status(),
                "save_failures": failures, "recovery_failures": self._recovery_failures}

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

    @measured("replay.view")
    def view(self, attempt_id: int) -> dict:
        """Clip metadata, cutting at most once per attempt at a time."""
        with self._session_gate.use(), self._cut_lock(attempt_id), self._pin_clip(attempt_id):
            return self._view(attempt_id)

    @measured("replay.prepare_view")
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
        extracted = False
        if saved is not None:
            m = self._saved_meta(saved)
            url, source = f"/api/replay/saved/{attempt_id}", "saved"
        elif clip.exists() and meta.exists():
            m = json.loads(meta.read_text())
            url, source = f"/api/replay/clips/{name}", "buffer"
        else:
            start, end = self._span(a)
            # Pin before waiting: pressure must not remove the beginning while
            # the last segment is still closing. Mapping uses the same lease.
            with self._pin_span(start, end):
                self._wait_for_tail(end)
                res = self.extractor.extract(self.recorder.ring, start, end, clip)
                captured = {}
                if res.start_utc is not None and res.duration_s and res.frame_times is not None:
                    self._map_from_feeds(captured, res)
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
                    # WHICH game frame each video frame shows: ONE path,
                    # and it is a read rather than a derivation. The
                    # capture layer stamps every picture with the frame
                    # the game submitted it as, read inside Project64;
                    # the feed log says which stamped row each encoded
                    # frame is; the map is those rows. A clip whose rows
                    # are not stamped (the capture layer was not running)
                    # gets NO map, and the panel says "Frame-exact capture
                    # is off" rather than showing a guess -- his standard
                    # for this surface: "We need 100% accuracy for this.
                    # If it's wrong even once, then it can't be relied on
                    # as a tool" (2026-08-23). Four generations of derived
                    # map lived here (frame-clock edges, the feed series,
                    # a pixel read, PJ64's present counter) plus a footage
                    # aligner, a clock join and a digit refit; all of them
                    # existed to recover an identity capture used to throw
                    # away, and the layer keeps it. Deleted 2026-09-05.
                    if res.frame_times is not None:
                        m.update(captured)
            extracted = True
            url, source = f"/api/replay/clips/{name}", "buffer"
        media_path = saved if source == "saved" else clip
        if "video_start_s" not in m:
            # Measure old scratch and saved clips alike, without rewriting
            # their archival sidecars. Assuming zero can seek one picture early.
            m["video_start_s"] = video_start_of(
                getattr(self.extractor, "ffmpeg", None), media_path)
        if m.get("frame_times") is not None and not valid_picture_times(m["frame_times"]):
            # A malformed stored clock cannot reach JSON/the browser. Recover
            # the picture times from the video when readable; retain the sidecar.
            measured = frame_times_of(getattr(self.extractor, "ffmpeg", None), media_path)
            m["frame_times"] = measured if valid_picture_times(measured) else None
        retained = m
        m = self._validated_meta(retained, a)
        if extracted:
            # Keep raw source evidence even when this interpretation cannot
            # expose a map. Future reads revalidate it, never trust saved claims.
            evidence = {key: retained[key] for key in ("picture_rows", "feed_match")
                        if key in retained}
            meta.write_text(json.dumps({**m, **evidence}))
            self._register_clip(a, clip)
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
                "attempt_start_slot": attempt_start_slot(m, a.anchor_frame),
                "input_span": captured_input_span(m),
                "frame_map": m.get("frame_map"),
                "frame_map_source": m.get("frame_map_source"),
                "input_alignment": m.get("input_alignment"),
                # Capture occurrence per slot, scoped to this clip. Heartbeats
                # keep the same ID; revisiting a raw counter gets another ID.
                "picture_ids": m.get("picture_rows"),
                # Exporters use the same captured occurrence, including across
                # repeated counters. A raw-frame lookup cannot recover this.
                "picture_states": _picture_states(m),
                # A capture-layer clip: the game's own timer per video
                # slot, from the stamps (None where a slot has none).
                "picture_igt": m.get("picture_igt"),
                # THE CLIP'S CHECK: the pad the plugin copied for each
                # picture against the input track at that frame -- what
                # the timeline's screen-check chip reads.
                "pad_stamp_agreement": m.get("pad_stamp_agreement"),
                "video_start_s": m.get("video_start_s", 0.0),
                # Per-frame timestamps of a picture-feed clip (VFR); None
                # for a CFR clip, whose slots are k / fps from
                # video_start_s.
                "frame_times": m.get("frame_times"),
                "encode": m.get("encode", "cfr"),
                # How much of the clip the feed log accounted for, and how
                # many rows the plugin could not call exact -- the capture's
                # own health, so a degraded run reads as degraded rather
                # than as a clean map.
                "feed_match": m.get("feed_match"),
                "plugin_inexact_rows": m.get("plugin_inexact_rows"),
                "saved_path": str(saved) if saved is not None else None}

    def _validated_meta(self, original: dict, attempt) -> dict:
        """Read cached evidence without changing its archival bytes.

        Every load interprets the retained source link again; stale derived
        maps/timers cannot become authoritative merely by being saved.
        """
        meta = dict(original)
        proposed = any(meta.get(key) is not None for key in (
            "frame_map", "picture_rows", "picture_igt", "media_clock"))
        problem = association_problem(meta) if proposed else "not_recorded"
        if problem is None and not self._rows_are_exact(meta):
            problem = "inexact_capture"
        for key in ("frame_map", "frame_map_source", "picture_igt", "state_rows",
                    "pad_stamp_agreement", "plugin_inexact_rows"):
            meta[key] = None
        if problem is not None:
            meta["picture_rows"] = None
            meta["feed_match"] = None
            meta["input_alignment"] = {
                "status": "unverified" if proposed else "unavailable", "reason": problem}
            return meta
        self._take_the_stamps(meta, attempt)
        meta["input_alignment"] = {"status": "source_linked"}
        return meta

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

    @measured("replay.associate_pictures")
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
        media_run = getattr(res, "media_run", None)
        source_pts = getattr(res, "source_pts", None)
        if media_run is None or source_pts is None:
            meta["feed_match"] = {"method": "source_pts",
                                  "reason": "missing_source_clock"}
            return
        try:
            feeds = ledger.feeds_between(start - 1.0, end + 1.0)
            # A cut may begin during a long hold. Its heartbeat names a row
            # captured well before the usual lead-in, so request that identity.
            row_start = min([start] + [entry["ts"] for entry in feeds
                            if entry.get("run_id") == media_run.id
                            and entry.get("ts") is not None]) - 1.5
            rows = ledger.rows_between(row_start, end + 1.0)
            if not rows or not feeds:
                return
            # An inexact row (two display lists between presents) claims
            # nothing. State interpretation uses this exact occurrence.
            row_index = {id(row): index for index, row in enumerate(rows)}
            matched_rows, repeats, stats = feed_map(
                source_pts, media_run.id, rows, feeds,
                lambda row: (row_index[id(row)]
                             if row.get("exact") and row.get("frame") is not None
                             else None))
        except Exception:
            log.exception("feed-log mapping failed; the clip carries no map")
            return
        meta["picture_ledger"] = [
            {**row, "ts": round(row["ts"] - start, 6)} for row in rows]
        meta["media_clock"] = {"version": 1, "run_id": media_run.id,
                               "origin_ts": media_run.origin_ts,
                               "source_pts": source_pts, "time_base": 90000}
        meta["feed_match"] = stats
        if matched_rows is None:
            log.warning("feed log covers too little of the clip: %s", stats)
            return
        meta["picture_rows"] = matched_rows
        meta["repeats"] = repeats
        meta["frame_map_source"] = "feed_log"
        log.info("frame map read off the feed log: %s", stats)

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

    def _take_the_stamps(self, meta: dict, attempt) -> None:
        """The map IS the rows: nothing is aligned, joined or read into
        identity. The stamp's own pad is then checked against the input
        track -- a per-picture agreement with no pixels in it."""
        rows = meta.get("picture_ledger") or []
        inexact = sum(1 for row in rows if not row.get("exact"))
        meta["frame_map_source"] = "plugin"
        meta["plugin_inexact_rows"] = inexact
        # The game's own timer in each picture: the IGT the plugin copied at
        # the list that drew it. The panel shows THIS as the frame's time,
        # so it reads what the screen printed rather than counting from the
        # track's first frame (the two clocks start a frame or two apart).
        # ...read off the row whose frame the map names for the slot, so
        # the clock and the pad the panel shows are the same picture's.
        # Resolve by capture occurrence, never by another visit to a raw
        # counter or a neighboring capture. Unknown slots stay unknown.
        state_rows = list(meta.get("picture_rows") or [])
        meta["frame_map"] = [rows[index]["frame"] if index is not None else None
                             for index in state_rows]
        meta["state_rows"] = state_rows
        igts = [rows[index].get("igt_overall") if index is not None else None
                for index in state_rows]
        meta["picture_igt"] = igts if any(igt is not None for igt in igts) else None
        self._audit_pad_stamps(meta, attempt)

    @measured("replay.audit_inputs")
    def _audit_pad_stamps(self, meta: dict, attempt) -> None:
        """THE CLIP'S OWN CHECK, and the only one that ships: every exact
        row's copied pad against the input track's pad at that frame, as
        `pad_stamp_agreement` = {pictures, agree, disagreements}.

        It replaced reading Usamune's input display out of the pixels
        (`replay/padread.py`, unwired 2026-09-05). Two facts decided it:
        the reader answers a question the stamps already answer exactly --
        does the timeline hold the pad the game held on this frame -- and
        it answered it badly, 82-94% on his three certified clips with
        every sampled disagreement a digit confusion off compressed video
        (L13 for L12, U83 for U82, a dropped direction letter). The stamp
        audit read 1,482 of 1,482 pictures on those same clips, costs no
        decode, and cannot misread. Absent when no track lookup is wired
        or the track is empty."""
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
        rows = meta.get("picture_ledger") or []
        slots = meta.get("state_rows") or []
        # Only represented exact captures describe a displayed picture. Ledger
        # rows can also include dropped grabs and inexact/unknown slots.
        occurrences = {}
        for index in slots:
            if index is not None:
                occurrences.setdefault(rows[index].get("frame"), set()).add(index)
        for slot, index in enumerate(slots):
            if index is None:
                continue
            row = rows[index]
            stamped = row.get("pad")
            frame = row.get("frame")
            if (not row.get("exact") or stamped is None or frame is None
                    or frame not in pads or len(occurrences[frame]) != 1):
                continue
            pictures += 1
            tracked = list(pads[frame])
            if tracked == list(stamped):
                agree += 1
            elif len(disagreements) < 50:
                disagreements.append([slot, frame, tracked, list(stamped)])
        # `pictures` is what could be CHECKED -- a clip reaches past the
        # attempt at both ends, and the track has no pad for those frames --
        # and `rows` is the clip's whole picture count, so the chip can say
        # how much of the clip the check covers instead of printing a ratio
        # of a number against itself. His 2026-09-01 ruling on the reader's
        # chip: "checked 1207/1207" was "literally and objectively wrong".
        meta["pad_stamp_agreement"] = {"pictures": pictures, "agree": agree,
                                       "rows": len(slots),
                                       "disagreements": disagreements}
        if pictures and agree != pictures:
            log.warning("pad stamps disagree with the track on %d of %d pictures",
                        pictures - agree, pictures)

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

    @measured("replay.wait_for_tail")
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

    def review_state(self, attempt_id: int) -> dict:
        """Preferences are readable before the first cut, without extraction."""
        with self._cut_lock(attempt_id):
            self._attempt(attempt_id)
            return self._review_state.get(attempt_id, self.find_saved(attempt_id))

    @property
    def review_session_token(self) -> str:
        return self._review_state.session_token

    def update_review_state(self, attempt_id: int, state: dict,
                            edit: str | None = None) -> dict:
        # Resolve the saved destination under the same lock as publication:
        # a PUT racing Save must not become temporary after promotion finishes.
        with self._cut_lock(attempt_id):
            self._attempt(attempt_id)
            return self._review_state.put(attempt_id, self.find_saved(attempt_id), state, edit)

    def save(self, attempt_id: int) -> dict:
        """Serialize extraction, publication, and review-state promotion."""
        with self._session_gate.use():
            return self._preserve(attempt_id)

    def _preserve(self, attempt_id: int) -> dict:
        with self._save_guard:
            self._active_saves += 1
        try:
            with self._cut_lock(attempt_id), self._pin_clip(attempt_id):
                result = self._save(attempt_id)
                self._review_state.promote(attempt_id, Path(result["path"]))
                with self._save_guard:
                    self._save_failures.pop(attempt_id, None)
                return result
        finally:
            with self._save_guard:
                self._active_saves -= 1

    def _save(self, attempt_id: int) -> dict:
        """Persist a clip to the permanent save tree (date/session/).

        Idempotent: an attempt that already has a saved file returns it
        as-is (re-saving with different pads = delete the file in Explorer
        first). Otherwise view() extracts the clip (cached when already
        cut) and publish its immutable bytes with a metadata sidecar — the sidecar is
        what makes the clip self-describing in later sessions, after the
        scratch cache and ring are gone.
        """
        a = self._attempt(attempt_id)
        existing = self.find_saved(attempt_id)
        if existing is not None:
            m = self._saved_meta(existing)
            return {"path": str(existing), "truncated": m.get("truncated", False)}
        self._view(attempt_id)  # caller holds the cut lock
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
        m = json.loads(clip.with_suffix(".json").read_text())
        # fps stamped at save time: the step buttons must match the clip's
        # actual encode rate even if cfg.fps changes in a future version.
        publish_saved(self.cfg.save_root, attempt_id, clip, dest,
                      {**m, "fps": self.cfg.fps}, self._review_state.get(attempt_id, None))
        forget = getattr(self.recorder.ring, "forget_temp", None)
        if forget is not None:
            forget(f"attempt:{attempt_id}", delete=True)
        else:
            clip.unlink(missing_ok=True)
            clip.with_suffix(".json").unlink(missing_ok=True)
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
            # A player already holding the temporary URL survives promotion.
            saved = self.find_saved(int(name.removeprefix("clip_attempt_").removesuffix(".mp4")))
            if saved is None:
                raise LookupError("no such clip")
            return saved
        return p

    @contextmanager
    def read_clip(self, name: str):
        """Keep temporary bytes alive through the entire HTTP range response."""
        if not _CLIP_RE.fullmatch(name):
            raise LookupError("no such clip")
        attempt_id = int(name.removeprefix("clip_attempt_").removesuffix(".mp4"))
        with self._pin_clip(attempt_id):
            yield self.clip_path(name)

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
        with self._session_gate.change():
            self._start_session()

    def _start_session(self) -> None:
        self._review_state.clear()
        self._recovery_failures = recover_saved(self.cfg.save_root)
        for failure in self._recovery_failures:
            log.error("%s", failure)
        # Start recorder first; it may wipe scratch_dir contents on init.
        # clips_dir is created after so a future recursive wipe doesn't
        # evict a directory we made first.
        self.recorder.start()
        self.clips_dir.mkdir(parents=True, exist_ok=True)

    def lifecycle_stop(self) -> None:
        with self._session_gate.change(close=True):
            try:
                self.recorder.stop()
            finally:
                self._review_state.clear()

    async def session_ended(self) -> None:
        """A user session switch ends scratch retention; resets and pauses do not."""
        await asyncio.to_thread(self._rotate_session)

    def _rotate_session(self) -> None:
        with self._session_gate.change():
            self.recorder.reset_session_scratch()
            self._review_state.clear()
