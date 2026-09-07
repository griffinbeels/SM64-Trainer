"""Orchestrator: window attach-retry (mirrors server/poller.py's pattern),
capture sources -> video sink / SegmentWriter -> SegmentRing, status
surface.

Two video paths:
- ffmpeg sink (PRIMARY when ffmpeg.exe is on PATH — main.py probes):
  _on_frame becomes a lock-free reference swap into the sink; pacing,
  encode and segmentation run in a child process (ffmpeg_sink.py
  docstring carries why in-process encoding was structurally glitchy:
  GIL co-tenancy).
- in-process fallback: the CFR-conform path below feeds SegmentWriter.

Threading: capture callbacks arrive on library threads (the video
source's deliver thread, the audio pump). One lock serialises writer
access; it is taken PER FRAME (not around whole CFR fill loops) so a
large fill after a stale-window gap can't starve the audio callback, and
the writer-None re-check happens UNDER the lock (teardown can run between
a naked check and the write).

CFR conform (fallback path only): frame_index =
round(seconds_since_anchor * fps). Small delivery gaps (sources send
frames only on change — pause menus, occlusion) are filled by re-encoding
the last frame at each missing index, up to one segment's worth of
frames. Larger gaps are NOT filled: the writer receives the real target
index, its gap-rotation logic detects the jump and rotates segments,
converting the silence into an honest coverage hole in the ring rather
than minutes of frozen duplicate video. This wall-clock-locks the video
stream, which is what makes utc <-> frame mapping exact.

Audio fallback chain: audio_factory is tried first; if its start() fails
and a fallback_audio_factory was provided, that is tried; otherwise
recording proceeds video-only. BOTH take the target window's pid — the
fallback needs it as much as the primary does, to target the endpoint
hosting that app's session. The chain is config wiring, not policy —
main.py decides the factories (currently: per-process tap PRIMARY so only
the game is recorded, device loopback as the fallback; see audio.py)."""
import logging
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

import numpy as np

from sm64_events.core.recorder_lock import acquire_recorder_lock
from sm64_events.replay.clock import CaptureClock, qpc_100ns
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.encoder import SegmentWriter, pick_video_codec
from sm64_events.replay.ledger import PictureLedger
from sm64_events.replay.ring import SegmentRing
from sm64_events.replay.window import WindowInfo

log = logging.getLogger("sm64.replay")

# Minimum idle threshold even when the padding window is tiny: prevents
# pause/resume thrash (each cycle restarts the ffmpeg child).
_IDLE_FLOOR_S = 3.0


class VideoSource(Protocol):
    """A camera. `on_frame(bgra, ts_100ns)` for a desktop grab the recorder
    must place in game time itself; the capture layer's source
    (replay/pluginsource.py) calls `on_frame(bgra, ts_100ns, stamp)` with the
    game's own frame counter and pad for that picture, and carries
    `frame_source = "plugin"` so status can say which camera is live."""
    def start(self, on_frame: Callable[..., None],
              on_stopped: Callable[[], None]) -> None: ...
    def stop(self) -> None: ...


class AudioSource(Protocol):
    mode: str  # "process" | "system"
    def start(self, on_pcm: Callable[[np.ndarray], None]) -> None: ...
    def stop(self) -> None: ...


def _sink_has_room(sink) -> bool:
    """Whether the sink can still encode a picture. A sink that does not
    answer (the CFR fallback, a test's stand-in) is always willing."""
    ask = getattr(sink, "has_room", None)
    return True if ask is None else bool(ask())


class ReplayRecorder:
    def __init__(self, cfg: ReplayConfig,
                 window_finder: Callable[[str], WindowInfo | None],
                 video_factory: Callable[[WindowInfo], VideoSource],
                 audio_factory: Callable[[int], AudioSource],
                 fallback_audio_factory: Callable[[int], AudioSource] | None = None,
                 clock_factory: Callable[[], CaptureClock] = CaptureClock.now,
                 codec: str | None = None,
                 video_sink_factory=None,
                 recorder_lock_factory=acquire_recorder_lock,
                 release_capture: Callable[[], None] | None = None):
        self._cfg = cfg
        # machine-wide single-recorder guard (injectable for tests): only the
        # instance holding this lock actually captures; others run viewer-only.
        self._recorder_lock_factory = recorder_lock_factory
        self._rec_lock = None        # held handle while WE are the recorder
        self._release_capture = release_capture
        self._capture_lock = threading.RLock()  # serialize attach vs teardown
        self._scratch_ready = False
        self._lock_warned = False    # log the viewer-only notice once
        self._window_finder = window_finder
        self._video_factory = video_factory
        self._audio_factory = audio_factory
        self._fallback_audio_factory = fallback_audio_factory
        self._clock_factory = clock_factory
        # The picture ledger (replay/ledger.py, item 40): one row per
        # DISTINCT captured picture -- its composition time, the RAM frame,
        # and every registered stamp. Extraction reads it back through
        # ReplayService._map_from_ledger.
        self.ledger = PictureLedger()
        self._ledger_name = f"picture-identity-{uuid4().hex}.sqlite3"
        # The picture feed (config.picture_feed, item 38): with the ffmpeg
        # sink, a grab reaches the encoder only when the ledger says it is
        # a NEW picture, and every write the sink completes lands in the
        # ledger's feed log -- one video frame, one row.
        self._picture_feed = bool(getattr(cfg, "picture_feed", False))
        # Grabs the sink had no budget to encode, so the ledger never saw
        # them either. status() reports it: a degraded capture must be a
        # number he can see, not a clip that merely looks thinner.
        self._grabs_skipped = 0
        self._codec: str | None = codec
        # ffmpeg-subprocess video path: when set, frames bypass the in-process
        # writer entirely (sink.submit is a lock-free reference swap; pacing,
        # encoding and segmentation happen in the child process — the GIL
        # decoupling that ended the scattered-missed-slot glitch class)
        self._video_sink_factory = video_sink_factory
        self._video_sink = None

        # free-disk-gated cap: the configured byte cap is a ceiling, but the
        # buffer never grows so large that the scratch volume's free space
        # drops below the ring's margin (a near-full disk thrashes the whole
        # machine — same symptom as a RAM leak).
        self.ring = SegmentRing(
            cfg.retention_s, cfg.max_buffer_bytes,
            free_bytes_fn=lambda: shutil.disk_usage(cfg.scratch_dir).free,
            on_evict=self.ledger.discard_segment)

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._window_lost = threading.Event()
        self._thread: threading.Thread | None = None
        self._stopping: bool = False  # M2: prevents zombie _begin_capture post-stop

        # protected by _lock
        self._writer: SegmentWriter | None = None
        self._video_source: VideoSource | None = None
        self._frame_source = "desktop"
        self._frame_source_note = None
        self._audio_source: AudioSource | None = None
        self._clock: CaptureClock | None = None
        self._last_frame: np.ndarray | None = None
        self._last_index: int = -1
        # observability: what the EYE sees is slots filled vs duplicated
        self._stat_written = 0   # real frames written to slots
        self._stat_fills = 0     # CFR duplicates (a missed slot each)
        self._stat_report_t = 0.0

        # status fields (written under _lock or from attach thread only)
        self._recording: bool = False
        self._window_found: bool = False
        self._audio_mode: str = "none"

        # idle gating: no player input for idle_after_s -> completed
        # segments are DISCARDED instead of retained (_on_segment); the
        # encoder keeps running so the timeline never breaks. First active
        # tick resumes instantly. These are plain attrs flipped from the
        # poll thread (resume) and attach thread (pause) — ref/bool/float
        # swaps are atomic in CPython, and both transitions are idempotent.
        self.set_idle_after(cfg.pre_pad_s + cfg.post_pad_s)
        self._last_player_active = time.monotonic()
        self._idle = False
        self._idle_since = None   # utc datetime while idle (the discard rule)
        self._idle_dropped = 0
        # Idle-discard unlinks that hit a Windows sharing violation (ffmpeg's
        # own segment close, a clip cut, an indexer briefly holding the file).
        # Deferred here and retried from the attach loop instead of
        # tracebacking: path -> monotonic time of the first failed attempt.
        self._deferred_discards: dict[Path, float] = {}
        self._discard_lock = threading.Lock()
        self._session_paused = False  # manual pause: outranks the input tap

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Start attach retries; only the recorder owner may reset scratch."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._scratch_ready = False
        self._stop_event.clear()
        self._stopping = False  # restart is supported; stop() sets this (M2 guard)
        self._thread = threading.Thread(
            target=self._attach_loop, name="replay-attach", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal attach loop to exit and tear down any active capture."""
        self._stopping = True  # M2: block any in-flight _begin_capture from racing post-stop
        self._stop_event.set()
        self._request_capture_stop()
        if self._thread is not None:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                log.warning("replay attach thread did not stop within timeout")
            self._thread = None
        self._teardown_capture()

    # -- attach loop ---------------------------------------------------------

    def _attach_loop(self) -> None:
        try:
            self._run_attach_loop()
        finally:
            self._teardown_capture()

    def _run_attach_loop(self) -> None:
        while not self._stop_event.is_set():
            win = self._window_finder(self._cfg.window_title)
            self._window_found = win is not None

            if win is not None and not self._recording:
                try:
                    self._begin_capture(win)
                except Exception:
                    log.exception("begin_capture failed — will retry")
                    self._teardown_capture()

            if self._window_lost.is_set():
                self._window_lost.clear()
                log.info("window lost — tearing down capture")
                self._teardown_capture()

            self._flush_deferred_discards()
            self._maybe_idle_pause()
            self._stop_event.wait(self._cfg.attach_poll_s)

    # -- capture setup / teardown --------------------------------------------

    def _prepare_scratch(self) -> None:
        if self._scratch_ready:
            if not self._picture_feed or (self._cfg.scratch_dir / self._ledger_name).exists():
                return
            # A different recorder owner reset shared scratch while we were
            # detached. Start a new owned lifetime instead of retaining ring
            # entries whose files and picture identities no longer exist.
            self._scratch_ready = False
        self.ledger.reset()
        scratch = self._cfg.scratch_dir
        # The machine-wide lock is already held. A viewer-only boot must
        # neither remove the owner's footage nor run this recorder's own
        # fallback codec probe (main may already have supplied a codec).
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True, exist_ok=True)
        self.ring.reset()
        with self._discard_lock:
            self._deferred_discards.clear()
        if self._codec is None:
            self._codec = pick_video_codec()
        self._scratch_ready = True

    def _begin_capture(self, win: WindowInfo) -> None:
        with self._capture_lock:
            try:
                self._start_capture(win)
            except BaseException:
                self._teardown_capture()
                raise
            if self._stopping:
                self._teardown_capture()

    def _start_capture(self, win: WindowInfo) -> None:
        # M2: bail immediately if stop() already ran — prevents a zombie begin
        # from racing ahead and resurrecting recording state post-stop.
        if self._stopping:
            return

        # Machine-wide single-recorder guard: if another tracker instance (a
        # second exe, a dev server in another worktree) already owns the
        # recorder lock, do NOT start a redundant capture of the same window —
        # that doubles GPU/encode/audio load and collides on the shared replay
        # buffer. Retried every attach cycle, so this instance takes over the
        # moment the owner exits (the OS frees the lock). Released in teardown.
        if self._rec_lock is None:
            self._rec_lock = self._recorder_lock_factory()
            if self._rec_lock is None:
                if not self._lock_warned:
                    log.warning("another tracker instance is already recording "
                                "this machine — running VIEWER-ONLY (no "
                                "capture); will take over if it exits")
                    self._lock_warned = True
                return
            self._lock_warned = False

        self._prepare_scratch()
        # Only the picture feed retains source-PTS identities. Legacy CFR
        # observations stay in the bounded diagnostic cache; persisting
        # them would imply a retention join its media never supplied.
        if self._picture_feed:
            self.ledger.open_archive(self._cfg.scratch_dir / self._ledger_name)

        clock = self._clock_factory()
        with self._lock:
            self._clock = clock
            self._writer = None
            self._last_frame = None
            self._last_index = -1

        # PRIMARY: one ffmpeg muxes video+audio on a single wall-clock (the AV
        # sink owns the timeline + audio sync). FALLBACK (no ffmpeg binary):
        # the in-process SegmentWriter — video plus the legacy count-based
        # audio sidecar, the only path that still has the old two-clock
        # behaviour, reached solely when no ffmpeg is present.
        if self._video_sink_factory is not None:
            # codec is supplied by main or picked on first owned capture:
            # the sink must encode with what THIS machine has, not with the
            # nvenc it used to hardcode — a non-NVIDIA machine's child died at
            # birth and the respawn loop flashed the user's cursor (2026-08-07).
            self._video_sink = self._video_sink_factory(self._cfg,
                                                        self._on_segment,
                                                        self._codec)
            self._video_sink.on_fed = self._on_fed
            self._video_sink.start()
        else:
            with self._lock:
                self._writer = SegmentWriter(
                    self._cfg, clock, self._cfg.scratch_dir, self._codec,
                    self._on_segment)

        # Start video — on_stopped signals window loss back to attach loop.
        # Publish before start: it may enable capture then fail, or overlap
        # stop(). Teardown must own even that partially started source.
        video = self._video_factory(win)
        with self._lock:
            self._video_source = video
        if self._stopping:
            return
        self._frame_source = getattr(video, "frame_source", "desktop")
        self._frame_source_note = getattr(video, "frame_source_note", None)
        # Idle throttle: while the recorder is idle (AFK / manual pause) the
        # capture source drops to a trickle grab rate — every segment is
        # discarded anyway, so the dominant cost (the per-grab ~8 MB surface
        # read+copy, ~2 GB/s at full rate) is pure waste. The ffmpeg feeder is
        # untouched, so resume stays seamless (no child respawn hole).
        if hasattr(video, "set_idle_check"):
            video.set_idle_check(self.is_idle)
        video.start(self._on_frame, self._window_lost.set)
        if self._stopping:
            return

        self._start_audio(win, clock)
        self._last_player_active = time.monotonic()  # fresh grace period
        # Startup can overlap a pause/unpause before the source is published.
        # Reconcile its actual demand after publication, including resume.
        self._set_idle(self._session_paused)
        self._recording = True
        log.info("capture started — window=%r audio=%s codec=%s",
                 win.title, self._audio_mode, self._codec)

    def _start_audio(self, win: WindowInfo, clock: CaptureClock) -> None:
        # Fallback-writer only: set its audio t0 BEFORE wiring any audio
        # callback so a PCM packet arriving between audio.start() and
        # start_audio() doesn't hit write_audio()'s "start_audio() not called"
        # guard. The AV sink needs none of this — ffmpeg stamps audio itself
        # by wall-clock, so there is no separate audio origin to reconcile (the
        # whole point: one clock, no two-stream A/V drift).
        if self._writer is not None:
            with self._lock:
                audio_t0 = clock.utc_of(qpc_100ns())
                self._writer.start_audio(t0_utc=audio_t0)
            log.info("AV-SYNC audio_t0=%s (in-process fallback)",
                     audio_t0.isoformat())

        # Audio fallback chain
        audio: AudioSource | None = None
        audio_mode = "none"
        try:
            audio = self._audio_factory(win.pid)
            audio.start(self._on_pcm)
            audio_mode = audio.mode
        except Exception:
            log.exception("primary audio source failed")
            if audio is not None:
                try:
                    audio.stop()
                except Exception:
                    log.exception("partially started audio source stop failed")
            if self._fallback_audio_factory is not None:
                try:
                    audio = self._fallback_audio_factory(win.pid)
                    audio.start(self._on_pcm)
                    audio_mode = audio.mode
                except Exception:
                    log.exception("fallback audio source also failed — video-only")
                    if audio is not None:
                        try:
                            audio.stop()
                        except Exception:
                            log.exception("partially started fallback audio stop failed")
                    audio = None
                    audio_mode = "none"
            else:
                audio = None
                audio_mode = "none"

        # C1 continued: assign _audio_source immediately after its start()
        # succeeds so teardown can reclaim it if anything after this raises.
        with self._lock:
            self._audio_source = audio
            self._audio_mode = audio_mode

    def _teardown_capture(self) -> None:
        with self._capture_lock:
            self._close_capture()

    def _request_capture_stop(self) -> None:
        source = self._video_source
        if source is not None and hasattr(source, "request_stop"):
            try:
                source.request_stop()
            except Exception:
                log.exception("capture demand revocation failed")

    def _close_capture(self) -> None:
        """Stop sources and close writer. Safe to call when already idle."""
        self._request_capture_stop()
        sink = self._video_sink
        self._video_sink = None
        with self._lock:
            video = self._video_source
            audio = self._audio_source
            writer = self._writer
            self._video_source = None
            self._audio_source = None
            self._writer = None
            self._clock = None

        for src in (video, audio):
            if src is not None:
                try:
                    src.stop()
                except Exception:
                    log.exception("error stopping source %r", src)

        if sink is not None:
            try:
                sink.stop()  # sources are quiet before draining the encoder
            except Exception:
                log.exception("ffmpeg sink stop failed")

        if writer is not None:
            try:
                writer.close()
            except Exception:
                log.exception("error closing writer")

        try:
            self.ledger.detach()
        except Exception:
            log.exception("picture archive close failed at capture teardown")

        self._recording = False
        self._idle = False
        self._idle_since = None
        # Release the machine-wide recorder lock so another instance can take
        # over recording (e.g. PJ64 closed here, or this instance is shutting
        # down). Re-acquired on the next _begin_capture if we capture again.
        if self._rec_lock is not None:
            try:
                if self._release_capture is not None:
                    self._release_capture()
            except Exception:
                log.exception("capture mapping release failed")
            finally:
                try:
                    self._rec_lock.close()
                except Exception:
                    log.exception("recorder lock release failed")
                self._rec_lock = None
        # _audio_mode intentionally kept as last-known value so status() can
        # report which mode was active even after stop; cleared only on
        # fresh _begin_capture (set to new mode) or explicit reset.

    # -- idle gating (no player input -> discard footage, don't retain) -------

    def set_idle_after(self, window_s: float) -> None:
        """The idle threshold tracks the user's padding window (pre+post):
        footage further from any input than the padding can never appear
        in a clip, so recording it is pure disk churn. Floored at
        _IDLE_FLOOR_S to prevent thrash at tiny pads."""
        self.idle_after_s = max(_IDLE_FLOOR_S, float(window_s))

    def set_player_active(self) -> None:
        """Poll-thread tap (replay/activity.py): called on every tick where
        the player is providing input. Resume happens HERE, instantly — the
        next attempt's pre-pad starts at the first input. Pause lives in
        the attach loop (2 s cadence; a couple of extra recorded idle
        seconds is harmless)."""
        if self._session_paused:
            return  # manual session pause outranks the input signal
        self._last_player_active = time.monotonic()
        if self._idle:
            self._set_idle(False)

    def set_session_paused(self, paused: bool) -> None:
        """Manual session pause (POST /api/pause via server/app.py): rides
        the idle-discard machinery — the encoder timeline stays unbroken,
        completed segments are dropped, the buffer gains nothing. Unlike
        auto-idle, resume is NOT input-driven (the poller pauses too, so
        the activity tap goes silent); unpausing restores recording
        immediately and refreshes the activity clock so auto-idle doesn't
        instantly re-trigger."""
        self._session_paused = paused
        if paused:
            if not self._idle:
                self._set_idle(True)
        else:
            self._last_player_active = time.monotonic()
            if self._idle:
                self._set_idle(False)

    def is_idle(self) -> bool:
        """True while footage is being discarded — auto-idle (AFK) OR manual
        session pause (both set _idle). The capture source reads this to
        throttle its grab rate; the gen-2 GC collector reads it to pick a
        free moment to run (a stop-the-world pause is invisible while idle)."""
        return self._idle

    def _maybe_idle_pause(self) -> None:
        if (self._recording and not self._idle
                and time.monotonic() - self._last_player_active
                > self.idle_after_s):
            self._set_idle(True)

    def _set_idle(self, idle: bool) -> None:
        self._idle = idle
        if idle:
            self._idle_since = datetime.now(timezone.utc)
            log.info("replay idle: no player input for %.0f s — new "
                     "segments will be discarded until input",
                     self.idle_after_s)
        else:
            self._idle_since = None
            log.info("replay idle: input detected — buffer resumes "
                     "(%d idle segments discarded)", self._idle_dropped)
            self._idle_dropped = 0

        source = self._video_source
        if source is not None and hasattr(source, "refresh_demand"):
            try:
                source.refresh_demand()
            except Exception:
                # A source closing during resume cannot interrupt game event
                # polling or leave the recorder halfway through its state change.
                log.exception("replay frame demand notification failed")

    def _on_segment(self, seg) -> None:
        """Ring gate — BOTH video segments and audio chunks arrive here.
        While idle, segments born ENTIRELY inside the idle window are
        deleted instead of retained; the encoder keeps running so the
        timeline never breaks. (Pausing the ffmpeg child was shipped first
        and reverted: every resume respawned it, leaving a ~0.2 s startup
        hole exactly where a 0-pre-pad clip begins — user-reported as a
        frozen clip opening.) Segments STRADDLING the idle boundary are
        kept: at pause they carry the last active footage; at resume they
        carry the anchor lead-up/fade-in, which is why a 0 s pre-pad clip
        opens exactly at the anchor."""
        idle_since = self._idle_since
        if idle_since is not None and seg.utc_start >= idle_since:
            self._idle_dropped += 1
            try:
                seg.path.unlink(missing_ok=True)
            except OSError:
                # A sharing violation, not a defect: ffmpeg's segment close, a
                # clip cut or an indexer still holds the file for a moment.
                # Defer to the attach loop rather than tracebacking (the old
                # ERROR here was reported as a bug in its own right,
                # 2026-08-07); start()'s scratch wipe is the last resort.
                with self._discard_lock:
                    self._deferred_discards.setdefault(seg.path,
                                                       time.monotonic())
                log.debug("idle-discard deferred (file busy): %s", seg.path)
            self.ledger.discard_segment(seg)
            return
        self.ring.add(seg)
        self.ledger.flush()

    # Give a held file this long to come free before we stop retrying and
    # leave it for the next start()'s scratch wipe.
    DISCARD_GIVE_UP_S = 300.0

    def _flush_deferred_discards(self) -> None:
        """Retry idle-discard unlinks that hit a sharing violation. Runs once
        per attach tick; the holder (ffmpeg, a clip cut) usually lets go
        within a segment or two."""
        with self._discard_lock:
            pending = list(self._deferred_discards.items())
        for path, first_failure in pending:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                if time.monotonic() - first_failure > self.DISCARD_GIVE_UP_S:
                    log.warning(
                        "idle-discard: %s still held %.0f s after the first "
                        "attempt — leaving it for the next startup wipe",
                        path, time.monotonic() - first_failure)
                else:
                    continue          # still busy — keep it queued
            with self._discard_lock:
                self._deferred_discards.pop(path, None)

    # -- frame callback (library thread) -------------------------------------

    def _observe_picture(self, bgra, tag, capture_ts, stamp) -> bool:
        """One grab into the picture ledger. A picture from THE CAPTURE
        LAYER carries its own stamp -- the game frame the plugin read
        inside Project64 at the display list that drew it -- and that row
        is what the frame map is made of. A grab with no stamp (the
        desktop camera, before the layer publishes) is recorded by TIME
        only: it names no frame, so the clip gets no map and the timeline
        says frame-exact capture is off rather than showing a guess."""
        if stamp is not None and tag is not None:
            return self.ledger.observe(bgra, tag[1], tag[0], stamp.extras())
        if capture_ts is not None:
            return self.ledger.observe(bgra, capture_ts, None)
        return False

    def _on_frame(self, bgra: np.ndarray, ts_100ns: int, stamp=None) -> None:
        # NO idle gate here: frames keep flowing so the sink's timeline and
        # `_latest` stay fresh; idle discard happens per completed segment
        # in _on_segment.
        # ffmpeg-sink path: a lock-free reference swap, nothing else — the
        # sink's feeder paces CFR and the child process encodes. The entire
        # in-process CFR/dedup/encode machinery below is bypassed.
        sink = self._video_sink
        if sink is not None:
            # Tag the picture AT CAPTURE (round 32 items 17 + 30): the RAM
            # frame current right now (map v2's key) and this picture's own
            # composition time -- WGC's SystemRelativeTime through the run's
            # CaptureClock, not the moment this callback happened to run --
            # which is what the frame map's present series keys on (v4).
            # A picture from the CAPTURE LAYER (item 95) arrives with its
            # own stamp -- the frame the game submitted it as, read inside
            # the emulator -- so the frame clock is not consulted for it:
            # the tag's frame IS the stamp's, and the row says `exact`.
            tag = None
            clock = self._clock
            capture_ts = (clock.utc_of(ts_100ns).timestamp()
                          if clock is not None else None)
            if stamp is not None and capture_ts is not None:
                tag = (stamp.frame, capture_ts)
            if self._picture_feed and not _sink_has_room(sink):
                # LOCKSTEP (item 88): no budget to encode this picture, so it
                # is not recorded as captured either. The ledger keeps its
                # promise -- every row it holds became a video frame -- and a
                # loaded machine yields a sparser clip rather than a clip
                # whose map describes frames the video does not contain. His
                # rule: "If I see a frame in my replay ... I would expect to
                # see the input capture for that frame as well."
                self._grabs_skipped += 1
                return
            # The picture ledger notices each NEW picture among the grabs
            # (item 40) -- before submit so the sample reads the buffer this
            # callback was handed. observe() never raises.
            new_picture = self._observe_picture(bgra, tag, capture_ts, stamp)
            if self._picture_feed:
                # ONE frame per DISTINCT picture (item 38): a grab that
                # changed nothing feeds nothing. The tag's second field is
                # the row's own ts, which the feed log keys on.
                if new_picture:
                    sink.submit(bgra, tag if tag is not None
                                else (None, capture_ts))
                return
            sink.submit(bgra, tag)
            return
        # M1: _last_frame and _last_index are written here only; WGC guarantees
        # a single callback thread, so they need no lock — if that ever changes,
        # move both inside _lock.
        with self._lock:
            if self._writer is None:
                return
            clock = self._clock

        target = round(clock.seconds_since_anchor(ts_100ns) * self._cfg.fps)

        # Drop backwards/duplicate (encoder will also guard, but be explicit)
        if target <= self._last_index:
            return

        # Fill small delivery gaps (WGC sends frames only on change) by
        # re-encoding the last frame; beyond one segment's worth, stop
        # pretending — hand the writer the real index and its gap-rotation
        # turns the silence into an honest coverage hole in the ring.
        max_fill = int(self._cfg.fps * self._cfg.segment_s)
        fill_from = (self._last_index + 1
                     if self._last_frame is not None
                     and target - self._last_index <= max_fill
                     else target)
        self._stat_fills += max(0, target - fill_from)
        self._stat_written += 1
        import time as _t
        now = _t.monotonic()
        if now - self._stat_report_t > 30:
            if self._stat_report_t:
                log.info("recorder video: %d slots written, %d CFR fills "
                         "(missed slots) in last 30s",
                         self._stat_written, self._stat_fills)
            self._stat_written = self._stat_fills = 0
            self._stat_report_t = now

        for idx in range(fill_from, target):
            with self._lock:
                if self._writer is None:
                    return
                self._writer.write_video(self._last_frame, idx)

        with self._lock:
            if self._writer is None:
                return
            self._writer.write_video(bgra, target)

        self._last_frame = bgra
        self._last_index = target

    def _on_fed(self, tag, wrote_at: float, *, media_run=None, pts=None) -> None:
        """The sink completed one write (feeder thread): file it under the
        row the tag names, or as a heartbeat repeat when there is none."""
        self.ledger.mark_fed(tag[1] if tag is not None else None, wrote_at,
                             media_run=media_run, pts=pts)

    # -- audio callback (library thread) -------------------------------------

    def _on_pcm(self, pcm_s16: np.ndarray) -> None:
        # PRIMARY: hand raw interleaved s16le PCM to the AV sink's audio pipe;
        # ffmpeg wall-clock-stamps it and aresample-locks it to the video
        # master, so there is no count-based cursor to keep advancing and no
        # idle gate (idle footage is discarded per completed segment, audio
        # included, in _on_segment). FALLBACK: the in-process count-based
        # writer (no ffmpeg).
        sink = self._video_sink
        if sink is not None:
            sink.submit_audio(pcm_s16.tobytes())
            return
        with self._lock:
            if self._writer is None:
                return
            self._writer.write_audio(pcm_s16)

    # -- status --------------------------------------------------------------

    def status(self) -> dict:
        # M3: reads of _recording/_window_found/_audio_mode/_codec are unlocked;
        # CPython attribute reads are atomic, so these are stale-but-never-torn —
        # acceptable for a polling status surface.
        cov = self.ring.coverage("video")
        return {
            "recording": self._recording,
            "window_found": self._window_found,
            "audio_mode": self._audio_mode,
            "encoder": self._codec,
            "buffer_start_utc": cov[0].isoformat() if cov else None,
            "buffer_end_utc": cov[1].isoformat() if cov else None,
            "disk_bytes": self.ring.total_bytes,
            "retention_s": self.ring.retention_s,
            "max_buffer_bytes": self.ring.max_bytes,
            "idle": self._idle,
            # A degraded capture is a NUMBER, not a thinner-looking clip:
            # grabs the sink had no budget for (so the ledger never recorded
            # them either) and how deep its queue is right now.
            "grabs_skipped": self._grabs_skipped,
            "encode_backlog": (self._video_sink.queue_depth()[0]
                               if hasattr(self._video_sink, "queue_depth")
                               else 0),
            # Which camera is live: "plugin" = the capture layer inside
            # Project64 (every picture stamped by the game), "desktop" = the
            # window grab the frame clock places in game time afterwards.
            "frame_source": self._frame_source,
            # ...and WHY it is the desktop when the capture layer is installed
            # (the layer refused every picture, presented none) -- None
            # when nothing fell back.
            "frame_source_note": getattr(self._video_source, "frame_source_note",
                                         self._frame_source_note),
            "frame_source_health": (self._video_source.status()
                                    if hasattr(self._video_source, "status")
                                    else None),
        }
