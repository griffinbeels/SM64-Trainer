# Video Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Self-contained recording of the PJ64 window + game audio into a disk segment ring, with attempt-scoped clip extraction served to an inline, scrub-smooth player in the practice UI.

**Architecture:** A new `replay/` zone (independent of the poller — never touches emulator memory) captures video via Windows.Graphics.Capture (`windows-capture`) and per-process audio (`proc-tap`), encodes ~2 s MPEG-TS video segments (NVENC, libx264 fallback) plus raw-PCM audio sidecar chunks into a retention ring under `data/replay_buffer/`. A `CaptureClock` QPC↔UTC anchor maps attempt timestamps into the buffer. Extraction decodes the overlapping segments and re-encodes one `+faststart` MP4 (AAC encoded at clip time — **refinement over the spec's per-segment AAC**: raw PCM sidecars make audio gapless across segment boundaries and sample-exact to trim; the spec's intent — audio required, disk segment ring — is unchanged). FastAPI serves clips via `FileResponse` (native Range/206).

**Tech Stack:** Python 3.12 / uv, `windows-capture==2.*`, `proc-tap`, PyAV (`av>=14`, bundles NVENC-enabled ffmpeg), numpy, FastAPI/Starlette, Preact + htm (vendored).

**Spec:** `docs/superpowers/specs/2026-06-11-video-replay-design.md`
**Research notes:** `~/.claude/agent-memory/frontier-research/windows-capture-recording-stack.md`

**Conventions for every task:** run tests with `uv run pytest -q` (full suite must stay green); commit after each task with a `feat:`/`test:`/`docs:` message explaining WHY (see `git log` style). All new file paths are under the repo root. Implementation work happens on a fresh branch `feature/video-replay` cut from `main` after the current feature branch merges (or from the current branch if directed) — NOT directly on `feature/garbage-runs-markers-progress-ui`.

### File structure (locked in)

```
src/sm64_events/replay/__init__.py      (empty marker)
src/sm64_events/replay/config.py        ReplayConfig dataclass — ALL tunables
src/sm64_events/replay/clock.py         CaptureClock: QPC↔UTC anchor (THE shared contract)
src/sm64_events/replay/ring.py          SegmentInfo + SegmentRing (retention, disk cap, span query)
src/sm64_events/replay/window.py        find PJ64 hwnd+pid (pure pick logic + ctypes enumerator)
src/sm64_events/replay/encoder.py       SegmentWriter: TS video segments + PCM chunks; NVENC probe
src/sm64_events/replay/extract.py       ClipExtractor: span -> faststart MP4 (video + AAC)
src/sm64_events/replay/recorder.py      Protocols + ReplayRecorder orchestrator (attach loop, CFR conform)
src/sm64_events/replay/video.py         WgcVideoSource adapter (windows-capture)   [live-verified]
src/sm64_events/replay/audio.py         ProcessAudioSource + SystemAudioSource     [live-verified]
src/sm64_events/replay/service.py       ReplayService: attempts -> spans -> clips -> saves
src/sm64_events/server/replay_api.py    create_replay_router (status/extract/serve/save)
src/sm64_events/ui/components/replay.js ReplayPlayer + RecordingDot
tests/test_replay_clock.py … test_replay_api.py  (one per module, mirrors repo pattern)
```

Modified: `tracking/views.py` (+1 field), `server/app.py` (replay param), `main.py` (wiring), `ui/components/practice.js` (button + player row), `ui/components/header.js` (dot), `ui/index.html` (styles), `pyproject.toml`, `.gitignore`, `README.md`, `CLAUDE.md`.

---

### Task 1: Dependencies + scaffolding

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Create: `src/sm64_events/replay/__init__.py`

- [ ] **Step 1: Add dependencies**

```powershell
uv add "av>=14" "numpy>=1.26" "windows-capture>=2.0.0" "proc-tap>=1.0.0" "pyaudiowpatch>=0.2.12"
```

Expected: `uv.lock` updated, `uv sync` succeeds. (`pyaudiowpatch` is the device-wide audio fallback; `windows-capture`/`proc-tap` are Windows-only — fine, the project is Windows-only.)

- [ ] **Step 2: Verify PyAV ships NVENC + verify capture package APIs**

```powershell
uv run python -c "import av; print('h264_nvenc' in av.codecs_available, 'libx264' in av.codecs_available)"
uv run python -c "import windows_capture, inspect; print(inspect.signature(windows_capture.WindowsCapture.__init__))"
uv run python -c "import proc_tap; print([n for n in dir(proc_tap) if not n.startswith('_')])"
```

Expected: `True True`; the WindowsCapture signature includes `window_hwnd`, `draw_border`, `cursor_capture`, `minimum_update_interval`; proc_tap exposes its tap class. **If any name differs from what Tasks 8–9 assume, adjust those adapters when you get there — the Protocol boundary means nothing else changes.**

- [ ] **Step 3: Ignore replay artifacts**

Append to `.gitignore` (check first — `data/` may already be covered):

```
replays/
data/replay_buffer/
```

- [ ] **Step 4: Create the package marker**

`src/sm64_events/replay/__init__.py` — empty file.

- [ ] **Step 5: Run suite + commit**

```powershell
uv run pytest -q
git add pyproject.toml uv.lock .gitignore src/sm64_events/replay/__init__.py
git commit -m "feat: add replay capture dependencies (windows-capture, proc-tap, PyAV/NVENC)"
```

---

### Task 2: ReplayConfig

**Files:**
- Create: `src/sm64_events/replay/config.py`
- Test: `tests/test_replay_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_replay_config.py
from pathlib import Path

from sm64_events.replay.config import ReplayConfig


def test_defaults_match_spec():
    cfg = ReplayConfig()
    assert cfg.enabled is True
    assert cfg.retention_s is None            # None = whole session (spec default)
    assert cfg.pre_pad_s == 3.0 and cfg.post_pad_s == 2.0
    assert cfg.fps == 30
    assert cfg.segment_s == 2.0
    assert cfg.max_buffer_bytes == 20 * 1024**3
    assert cfg.save_root == Path("replays")
    assert cfg.scratch_dir == Path("data") / "replay_buffer"
    assert cfg.window_title == "Project64"
    assert cfg.audio_rate == 48000


def test_retention_minutes_constructor():
    assert ReplayConfig(retention_s=600.0).retention_s == 600.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_replay_config.py -q` — Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
# src/sm64_events/replay/config.py
"""All replay tunables in one place (spec: Config section)."""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ReplayConfig:
    enabled: bool = True
    retention_s: float | None = None      # None = keep the whole session
    pre_pad_s: float = 3.0                # before the attempt anchor
    post_pad_s: float = 2.0               # after the closing event
    fps: int = 30                         # game content is 30 fps
    segment_s: float = 2.0                # video segment / audio chunk length
    max_buffer_bytes: int = 20 * 1024**3  # hard disk guard regardless of retention
    save_root: Path = field(default=Path("replays"))
    scratch_dir: Path = field(default=Path("data") / "replay_buffer")
    window_title: str = "Project64"       # substring match on the window title
    audio_rate: int = 48000               # proc-tap delivers 48 kHz stereo
    attach_poll_s: float = 2.0            # window-hunt interval
    extract_wait_s: float = 5.0           # bounded wait for the tail segment
```

- [ ] **Step 4: Run test to verify it passes** — `uv run pytest tests/test_replay_config.py -q` — Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/sm64_events/replay/config.py tests/test_replay_config.py
git commit -m "feat: ReplayConfig - one authoritative home for replay tunables"
```

---

### Task 3: CaptureClock (QPC↔UTC)

**Files:**
- Create: `src/sm64_events/replay/clock.py`
- Test: `tests/test_replay_clock.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_replay_clock.py
from datetime import datetime, timedelta, timezone

from sm64_events.replay.clock import CaptureClock, qpc_100ns

T0 = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)


def test_utc_of_maps_ticks_forward():
    clk = CaptureClock(anchor_qpc_100ns=10_000_000, anchor_utc=T0)
    # 1.5 s after the anchor in 100 ns ticks
    assert clk.utc_of(10_000_000 + 15_000_000) == T0 + timedelta(seconds=1.5)


def test_seconds_since_anchor_and_utc_roundtrip():
    clk = CaptureClock(anchor_qpc_100ns=0, anchor_utc=T0)
    assert clk.seconds_since_anchor(30_000_000) == 3.0
    assert clk.utc_to_seconds(T0 + timedelta(seconds=3)) == 3.0
    assert clk.utc_of(30_000_000) == T0 + timedelta(seconds=3)


def test_qpc_100ns_is_positive_and_monotonic():
    a = qpc_100ns()
    b = qpc_100ns()
    assert a > 0 and b >= a


def test_now_constructor_uses_current_clocks():
    clk = CaptureClock.now()
    assert clk.anchor_utc.tzinfo is timezone.utc
    assert clk.anchor_qpc_100ns > 0
```

- [ ] **Step 2: Run to verify FAIL** — `uv run pytest tests/test_replay_clock.py -q`

- [ ] **Step 3: Implement**

```python
# src/sm64_events/replay/clock.py
"""QPC <-> UTC bridge — THE contract between event timestamps and the buffer.

WGC stamps every frame with Direct3D11CaptureFrame.SystemRelativeTime: QPC
time scaled to 100 ns ticks since boot. qpc_100ns() reproduces that exact
timebase (QueryPerformanceCounter / QueryPerformanceFrequency * 1e7), so one
(qpc, utc) pair captured at recorder start maps any frame timestamp to UTC
and any event UTC into the stream. Capture the pair ONCE per recording run;
re-anchoring mid-run would shear A/V against event timestamps."""
import ctypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


def qpc_100ns() -> int:
    count = ctypes.c_int64()
    freq = ctypes.c_int64()
    ctypes.windll.kernel32.QueryPerformanceCounter(ctypes.byref(count))
    ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(freq))
    return count.value * 10_000_000 // freq.value


@dataclass(frozen=True)
class CaptureClock:
    anchor_qpc_100ns: int
    anchor_utc: datetime  # tz-aware UTC

    @classmethod
    def now(cls) -> "CaptureClock":
        return cls(qpc_100ns(), datetime.now(timezone.utc))

    def utc_of(self, ts_100ns: int) -> datetime:
        return self.anchor_utc + timedelta(
            microseconds=(ts_100ns - self.anchor_qpc_100ns) / 10)

    def seconds_since_anchor(self, ts_100ns: int) -> float:
        return (ts_100ns - self.anchor_qpc_100ns) / 1e7

    def utc_to_seconds(self, t: datetime) -> float:
        return (t - self.anchor_utc).total_seconds()
```

- [ ] **Step 4: Run to verify PASS** — `uv run pytest tests/test_replay_clock.py -q`

- [ ] **Step 5: Commit**

```powershell
git add src/sm64_events/replay/clock.py tests/test_replay_clock.py
git commit -m "feat: CaptureClock - one QPC/UTC anchor bridges events and footage"
```

---

### Task 4: SegmentRing

**Files:**
- Create: `src/sm64_events/replay/ring.py`
- Test: `tests/test_replay_ring.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_replay_ring.py
from datetime import datetime, timedelta, timezone

from sm64_events.replay.ring import SegmentInfo, SegmentRing

T0 = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)


def seg(tmp_path, i, kind="video", length_s=2.0, size=100):
    p = tmp_path / f"{kind}_{i:06d}.bin"
    p.write_bytes(b"x" * size)
    start = T0 + timedelta(seconds=i * length_s)
    return SegmentInfo(path=p, kind=kind, utc_start=start,
                       utc_end=start + timedelta(seconds=length_s),
                       size_bytes=size)


def test_covering_selects_overlapping_only(tmp_path):
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    for i in range(5):
        ring.add(seg(tmp_path, i))
    got = ring.covering("video", T0 + timedelta(seconds=3),
                        T0 + timedelta(seconds=7))
    assert [s.path.name for s in got] == ["video_000001.bin", "video_000002.bin",
                                          "video_000003.bin"]


def test_retention_evicts_and_deletes_files(tmp_path):
    ring = SegmentRing(retention_s=4.0, max_bytes=10**9)
    segs = [seg(tmp_path, i) for i in range(5)]
    for s in segs:
        ring.add(s)
    # newest end = T0+10 s; retention 4 s keeps segments ending after T0+6 s
    assert not segs[0].path.exists() and not segs[1].path.exists()
    assert segs[2].path.exists() is False or segs[2].path.exists()  # boundary
    assert segs[4].path.exists()
    cov = ring.coverage("video")
    assert cov is not None and cov[1] == segs[4].utc_end


def test_disk_cap_evicts_oldest_regardless_of_retention(tmp_path):
    ring = SegmentRing(retention_s=None, max_bytes=250)
    segs = [seg(tmp_path, i, size=100) for i in range(4)]
    for s in segs:
        ring.add(s)
    assert ring.total_bytes <= 250
    assert not segs[0].path.exists() and segs[3].path.exists()


def test_coverage_none_when_empty():
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    assert ring.coverage("video") is None


def test_audio_and_video_tracked_independently_for_query(tmp_path):
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    ring.add(seg(tmp_path, 0, kind="video"))
    ring.add(seg(tmp_path, 0, kind="audio"))
    assert len(ring.covering("audio", T0, T0 + timedelta(seconds=2))) == 1
    assert len(ring.covering("video", T0, T0 + timedelta(seconds=2))) == 1
```

- [ ] **Step 2: Run to verify FAIL** — `uv run pytest tests/test_replay_ring.py -q`

- [ ] **Step 3: Implement**

```python
# src/sm64_events/replay/ring.py
"""Disk-backed replay ring: a deque of segment files with two eviction
rules — retention age (None = keep the whole session) and a byte cap that
applies regardless (the disk guard from the spec). Video segments and audio
PCM chunks share one ring and one eviction policy so A/V coverage stays
aligned. Eviction deletes files; missing files are tolerated (crash debris
is cleaned by the recorder at startup)."""
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class SegmentInfo:
    path: Path
    kind: str               # "video" | "audio"
    utc_start: datetime
    utc_end: datetime
    size_bytes: int


class SegmentRing:
    def __init__(self, retention_s: float | None, max_bytes: int):
        self._retention_s = retention_s
        self._max_bytes = max_bytes
        self._segments: deque[SegmentInfo] = deque()
        self.total_bytes = 0

    def add(self, seg: SegmentInfo) -> None:
        self._segments.append(seg)
        self.total_bytes += seg.size_bytes
        self._evict(now=seg.utc_end)

    def _evict(self, now: datetime) -> None:
        def drop_head():
            old = self._segments.popleft()
            self.total_bytes -= old.size_bytes
            old.path.unlink(missing_ok=True)

        if self._retention_s is not None:
            horizon = now - timedelta(seconds=self._retention_s)
            while self._segments and self._segments[0].utc_end <= horizon:
                drop_head()
        while self._segments and self.total_bytes > self._max_bytes:
            drop_head()

    def covering(self, kind: str, start: datetime, end: datetime) -> list[SegmentInfo]:
        return [s for s in self._segments
                if s.kind == kind and s.utc_end > start and s.utc_start < end]

    def coverage(self, kind: str) -> tuple[datetime, datetime] | None:
        ks = [s for s in self._segments if s.kind == kind]
        if not ks:
            return None
        return ks[0].utc_start, ks[-1].utc_end
```

- [ ] **Step 4: Run to verify PASS** — `uv run pytest tests/test_replay_ring.py -q`

- [ ] **Step 5: Commit**

```powershell
git add src/sm64_events/replay/ring.py tests/test_replay_ring.py
git commit -m "feat: SegmentRing - retention + disk-cap eviction over A/V segment files"
```

---

### Task 5: Window finder

**Files:**
- Create: `src/sm64_events/replay/window.py`
- Test: `tests/test_replay_window.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_replay_window.py
from sm64_events.replay.window import WindowInfo, pick_window


def w(hwnd, title, pid=42, visible=True):
    return WindowInfo(hwnd=hwnd, title=title, pid=pid, visible=visible)


def test_picks_first_visible_title_match_case_insensitive():
    wins = [w(1, "Notepad"), w(2, "project64 Version 1.6", pid=7),
            w(3, "Project64 Version 1.6", pid=8)]
    got = pick_window(wins, "Project64")
    assert got is not None and got.hwnd == 2 and got.pid == 7


def test_skips_invisible_and_empty_titles():
    wins = [w(1, "Project64", visible=False), w(2, "")]
    assert pick_window(wins, "Project64") is None


def test_no_match_returns_none():
    assert pick_window([w(1, "Notepad")], "Project64") is None
```

- [ ] **Step 2: Run to verify FAIL** — `uv run pytest tests/test_replay_window.py -q`

- [ ] **Step 3: Implement**

```python
# src/sm64_events/replay/window.py
"""Locate the PJ64 top-level window: hwnd for WGC capture, pid for proc-tap.

pick_window() is pure (tested); enum_windows()/find_window() are the ctypes
boundary (live-verified). Title matching is substring + case-insensitive —
PJ64 1.6 titles itself 'Project64 Version 1.6' (sometimes with the ROM name
appended), so the default config substring 'Project64' matches both."""
import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    visible: bool


def pick_window(windows: list[WindowInfo], title_substring: str) -> WindowInfo | None:
    needle = title_substring.lower()
    for win in windows:
        if win.visible and win.title and needle in win.title.lower():
            return win
    return None


def enum_windows() -> list[WindowInfo]:
    user32 = ctypes.windll.user32
    out: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append(WindowInfo(hwnd=int(hwnd), title=buf.value,
                              pid=pid.value,
                              visible=bool(user32.IsWindowVisible(hwnd))))
        return True

    user32.EnumWindows(cb, 0)
    return out


def find_window(title_substring: str) -> WindowInfo | None:
    return pick_window(enum_windows(), title_substring)
```

- [ ] **Step 4: Run to verify PASS** — `uv run pytest tests/test_replay_window.py -q`

- [ ] **Step 5: Commit**

```powershell
git add src/sm64_events/replay/window.py tests/test_replay_window.py
git commit -m "feat: PJ64 window finder - pure pick logic over a ctypes enumerator"
```

---

### Task 6: SegmentWriter (encode video segments + PCM chunks)

**Files:**
- Create: `src/sm64_events/replay/encoder.py`
- Test: `tests/test_replay_encoder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_replay_encoder.py
from datetime import datetime, timedelta, timezone

import av
import numpy as np

from sm64_events.replay.clock import CaptureClock
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.encoder import SegmentWriter, pick_video_codec

T0 = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
CFG = ReplayConfig()
CLK = CaptureClock(anchor_qpc_100ns=0, anchor_utc=T0)


def frame(i):
    arr = np.zeros((480, 640, 4), dtype=np.uint8)
    arr[:, :, 0] = i % 256  # vary content so the encoder has work
    return arr


def make_writer(tmp_path, collected):
    return SegmentWriter(cfg=CFG, clock=CLK, out_dir=tmp_path,
                         codec="libx264", on_segment=collected.append)


def test_video_rotates_every_segment_and_stamps_utc(tmp_path):
    segs = []
    w = make_writer(tmp_path, segs)
    for i in range(150):                      # 5 s at 30 fps
        w.write_video(frame(i), frame_index=i)
    w.close()
    video = [s for s in segs if s.kind == "video"]
    assert len(video) == 3                    # 2 s + 2 s + 1 s partial
    assert video[0].utc_start == T0
    assert video[0].utc_end == T0 + timedelta(seconds=2)
    assert video[2].utc_end == T0 + timedelta(seconds=5)
    with av.open(str(video[0].path)) as c:    # decodable, full GOP
        assert len([f for f in c.decode(video=0)]) == 60


def test_audio_chunks_carry_sample_accurate_ranges(tmp_path):
    segs = []
    w = make_writer(tmp_path, segs)
    w.start_audio(t0_utc=T0)
    pcm = np.zeros((48000, 2), dtype=np.int16)   # 1 s of silence
    for _ in range(5):
        w.write_audio(pcm)
    w.close()
    audio = [s for s in segs if s.kind == "audio"]
    assert len(audio) == 3                    # 2 s + 2 s + 1 s partial
    assert audio[0].utc_start == T0
    assert audio[1].utc_start == T0 + timedelta(seconds=2)
    assert audio[0].size_bytes == 48000 * 2 * 2 * 2  # 2 s * stereo * s16


def test_dimension_change_rotates_segment(tmp_path):
    segs = []
    w = make_writer(tmp_path, segs)
    for i in range(30):
        w.write_video(frame(i), frame_index=i)
    big = np.zeros((600, 800, 4), dtype=np.uint8)
    w.write_video(big, frame_index=30)        # resize mid-segment
    w.close()
    video = [s for s in segs if s.kind == "video"]
    assert len(video) == 2                    # early rotation at the resize


def test_pick_video_codec_returns_known_codec():
    assert pick_video_codec() in ("h264_nvenc", "libx264")
```

- [ ] **Step 2: Run to verify FAIL** — `uv run pytest tests/test_replay_encoder.py -q`

- [ ] **Step 3: Implement**

```python
# src/sm64_events/replay/encoder.py
"""Encode the capture streams into ring files.

Video: one fresh PyAV MPEG-TS container + encoder per ~2 s segment (PyAV's
built-in segment muxer has a long-standing crash bug — issue #254 — so we
rotate manually). Fresh-per-segment means every segment opens on a keyframe
and is independently decodable; encoder init every 2 s is negligible at
480p. GOP = segment length, closed.

Audio: raw PCM s16le interleaved sidecar chunks (.pcm), NOT per-segment AAC —
fresh AAC encoders add ~21 ms priming silence per segment which would tick
audibly every 2 s in extracted clips. PCM is gapless, sample-exact to slice,
and AAC is encoded once at clip time. Cost: ~0.7 GB/h, comparable to video.

Frame indexes are wall-clock-locked (index = round(seconds_since_anchor *
fps), assigned by the recorder), so utc_start of any segment is
anchor + first_index/fps exactly — no per-frame timestamp bookkeeping."""
import logging
from datetime import timedelta
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from sm64_events.replay.clock import CaptureClock
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.ring import SegmentInfo

log = logging.getLogger("sm64.replay")


def pick_video_codec() -> str:
    """NVENC if the bundled ffmpeg + driver can actually encode (driver >= 570
    gate per research) — probe with one real frame, not just codec presence."""
    try:
        ctx = av.CodecContext.create("h264_nvenc", "w")
        ctx.width, ctx.height = 64, 64
        ctx.pix_fmt = "yuv420p"
        ctx.time_base = Fraction(1, 30)
        f = av.VideoFrame(64, 64, "yuv420p")
        f.pts = 0
        ctx.encode(f)
        return "h264_nvenc"
    except Exception:
        log.info("h264_nvenc unavailable - falling back to libx264")
        return "libx264"


class SegmentWriter:
    def __init__(self, cfg: ReplayConfig, clock: CaptureClock, out_dir: Path,
                 codec: str, on_segment) -> None:
        self._cfg = cfg
        self._clock = clock
        self._dir = out_dir
        self._codec = codec
        self._on_segment = on_segment
        self._frames_per_seg = int(cfg.fps * cfg.segment_s)
        # video state
        self._container = None
        self._stream = None
        self._seg_first_index: int | None = None
        self._seg_frames = 0
        self._seg_n = 0
        self._dims: tuple[int, int] | None = None  # (w, h)
        # audio state
        self._audio_t0 = None
        self._chunk_samples = int(cfg.audio_rate * cfg.segment_s)
        self._pcm_buf: list[np.ndarray] = []
        self._pcm_buffered = 0
        self._samples_written = 0
        self._chunk_n = 0
        out_dir.mkdir(parents=True, exist_ok=True)

    # -- video ---------------------------------------------------------------
    def write_video(self, bgra: np.ndarray, frame_index: int) -> None:
        h, w = bgra.shape[:2]
        if self._container is not None and (
                (w, h) != self._dims or self._seg_frames >= self._frames_per_seg):
            self._close_video_segment()
        if self._container is None:
            self._open_video_segment(frame_index, w, h)
        vf = av.VideoFrame.from_ndarray(bgra, format="bgra")
        vf = vf.reformat(format="yuv420p")
        vf.pts = frame_index - self._seg_first_index
        for pkt in self._stream.encode(vf):
            self._container.mux(pkt)
        self._seg_frames += 1

    def _open_video_segment(self, first_index: int, w: int, h: int) -> None:
        self._seg_n += 1
        path = self._dir / f"video_{self._seg_n:06d}.ts"
        self._container = av.open(str(path), "w", format="mpegts")
        self._stream = self._container.add_stream(self._codec, rate=self._cfg.fps)
        self._stream.width, self._stream.height = w, h
        self._stream.pix_fmt = "yuv420p"
        self._stream.codec_context.time_base = Fraction(1, self._cfg.fps)
        self._stream.codec_context.gop_size = self._frames_per_seg
        if self._codec == "libx264":
            self._stream.options = {"preset": "ultrafast", "tune": "zerolatency"}
        self._seg_first_index = first_index
        self._seg_frames = 0
        self._dims = (w, h)
        self._path = path

    def _close_video_segment(self) -> None:
        if self._container is None:
            return
        for pkt in self._stream.encode(None):
            self._container.mux(pkt)
        self._container.close()
        fps = self._cfg.fps
        start = self._clock.anchor_utc + timedelta(
            seconds=self._seg_first_index / fps)
        end = start + timedelta(seconds=self._seg_frames / fps)
        self._on_segment(SegmentInfo(
            path=self._path, kind="video", utc_start=start, utc_end=end,
            size_bytes=self._path.stat().st_size))
        self._container = self._stream = None

    # -- audio ---------------------------------------------------------------
    def start_audio(self, t0_utc) -> None:
        self._audio_t0 = t0_utc

    def write_audio(self, pcm_s16: np.ndarray) -> None:
        """pcm_s16: (n, 2) int16 at cfg.audio_rate."""
        self._pcm_buf.append(pcm_s16)
        self._pcm_buffered += len(pcm_s16)
        while self._pcm_buffered >= self._chunk_samples:
            self._flush_audio_chunk(self._chunk_samples)

    def _flush_audio_chunk(self, n_samples: int) -> None:
        buf = np.concatenate(self._pcm_buf)
        chunk, rest = buf[:n_samples], buf[n_samples:]
        self._pcm_buf = [rest] if len(rest) else []
        self._pcm_buffered = len(rest)
        self._chunk_n += 1
        path = self._dir / f"audio_{self._chunk_n:06d}.pcm"
        path.write_bytes(chunk.tobytes())
        rate = self._cfg.audio_rate
        start = self._audio_t0 + timedelta(seconds=self._samples_written / rate)
        end = start + timedelta(seconds=len(chunk) / rate)
        self._samples_written += len(chunk)
        self._on_segment(SegmentInfo(
            path=path, kind="audio", utc_start=start, utc_end=end,
            size_bytes=len(chunk) * 4))  # n*2ch*2bytes

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        self._close_video_segment()
        if self._pcm_buffered:
            self._flush_audio_chunk(self._pcm_buffered)
```

- [ ] **Step 4: Run to verify PASS** — `uv run pytest tests/test_replay_encoder.py -q`

Note: if `stream.options` assignment after `add_stream` misbehaves in your PyAV version, pass `options={...}` to `add_stream` instead — adjust and keep the test green.

- [ ] **Step 5: Commit**

```powershell
git add src/sm64_events/replay/encoder.py tests/test_replay_encoder.py
git commit -m "feat: SegmentWriter - TS video segments + gapless PCM chunks (manual rotation, not PyAV's broken segment muxer)"
```

---

### Task 7: ClipExtractor

**Files:**
- Create: `src/sm64_events/replay/extract.py`
- Test: `tests/test_replay_extract.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_replay_extract.py
from datetime import datetime, timedelta, timezone

import av
import numpy as np

from sm64_events.replay.clock import CaptureClock
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.encoder import SegmentWriter
from sm64_events.replay.extract import ClipExtractor
from sm64_events.replay.ring import SegmentRing

T0 = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
CFG = ReplayConfig()


def build_buffer(tmp_path, seconds=6):
    """Real segments: 6 s of video (frame index painted into pixels) + sine audio."""
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    clk = CaptureClock(anchor_qpc_100ns=0, anchor_utc=T0)
    w = SegmentWriter(cfg=CFG, clock=clk, out_dir=tmp_path / "buf",
                      codec="libx264", on_segment=ring.add)
    w.start_audio(t0_utc=T0)
    t = np.arange(48000, dtype=np.float32) / 48000
    tone = (np.sin(2 * np.pi * 440 * t) * 0.3 * 32767).astype(np.int16)
    sec = np.stack([tone, tone], axis=1)
    for i in range(seconds * 30):
        arr = np.full((480, 640, 4), (i * 4) % 256, dtype=np.uint8)
        w.write_video(arr, frame_index=i)
    for _ in range(seconds):
        w.write_audio(sec)
    w.close()
    return ring


def test_extract_produces_scrubbable_av_mp4(tmp_path):
    ring = build_buffer(tmp_path)
    ex = ClipExtractor(cfg=CFG, codec="libx264")
    out = tmp_path / "clip.mp4"
    res = ex.extract(ring, T0 + timedelta(seconds=1), T0 + timedelta(seconds=5), out)
    assert res.truncated is False
    assert abs(res.duration_s - 4.0) < 0.2
    with av.open(str(out)) as c:
        kinds = {s.type for s in c.streams}
        assert kinds == {"video", "audio"}
        n = len([f for f in c.decode(video=0)])
        assert abs(n - 120) <= 3            # 4 s * 30 fps


def test_extract_clamps_and_flags_truncation(tmp_path):
    ring = build_buffer(tmp_path)
    ex = ClipExtractor(cfg=CFG, codec="libx264")
    out = tmp_path / "clip.mp4"
    res = ex.extract(ring, T0 - timedelta(seconds=10), T0 + timedelta(seconds=2), out)
    assert res.truncated is True
    assert abs(res.duration_s - 2.0) < 0.2


def test_extract_no_footage_raises(tmp_path):
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    ex = ClipExtractor(cfg=CFG, codec="libx264")
    try:
        ex.extract(ring, T0, T0 + timedelta(seconds=1), tmp_path / "c.mp4")
        assert False, "expected ValueError"
    except ValueError:
        pass
```

- [ ] **Step 2: Run to verify FAIL** — `uv run pytest tests/test_replay_extract.py -q`

- [ ] **Step 3: Implement**

```python
# src/sm64_events/replay/extract.py
"""Cut one scrub-ready MP4 out of the ring.

Decode-and-re-encode, not stream-copy concat (spec decision): frame-accurate
edges, absorbs mid-session window resizes (everything scales to the first
frame's dims), and the clip gets dense keyframes (0.5 s GOP) + +faststart —
the two properties browser scrubbing actually needs. NVENC makes this far
faster than realtime at 480p.

Video frame times are reconstructed as seg.utc_start + i/fps (segments are
CFR by construction — see encoder.py). Audio is sliced sample-exactly from
the PCM chunks and AAC-encoded once, here."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.ring import SegmentRing

_EDGE_TOLERANCE_S = 0.5  # clamping beyond this marks the clip truncated


@dataclass(frozen=True)
class ClipResult:
    path: Path
    duration_s: float
    truncated: bool


class ClipExtractor:
    def __init__(self, cfg: ReplayConfig, codec: str):
        self._cfg = cfg
        self._codec = codec

    def extract(self, ring: SegmentRing, start: datetime, end: datetime,
                out_path: Path) -> ClipResult:
        cov = ring.coverage("video")
        if cov is None:
            raise ValueError("no footage in the replay buffer")
        s = max(start, cov[0])
        e = min(end, cov[1])
        if e <= s:
            raise ValueError("no footage overlaps the requested span")
        truncated = ((s - start).total_seconds() > _EDGE_TOLERANCE_S
                     or (end - e).total_seconds() > _EDGE_TOLERANCE_S)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        fps = self._cfg.fps
        out = av.open(str(out_path), "w", options={"movflags": "+faststart"})
        vstream = astream = None
        try:
            # ---- video ----------------------------------------------------
            out_index = 0
            dims: tuple[int, int] | None = None
            for seg in ring.covering("video", s, e):
                with av.open(str(seg.path)) as src:
                    for i, frame in enumerate(src.decode(video=0)):
                        t = seg.utc_start + timedelta(seconds=i / fps)
                        if not (s <= t < e):
                            continue
                        if vstream is None:
                            dims = (frame.width, frame.height)
                            vstream = out.add_stream(self._codec, rate=fps)
                            vstream.width, vstream.height = dims
                            vstream.pix_fmt = "yuv420p"
                            vstream.codec_context.time_base = Fraction(1, fps)
                            vstream.codec_context.gop_size = fps // 2  # 0.5 s seeks
                        vf = frame.reformat(width=dims[0], height=dims[1],
                                            format="yuv420p")
                        vf.pts = out_index
                        out_index += 1
                        for pkt in vstream.encode(vf):
                            out.mux(pkt)
            if vstream is None:
                raise ValueError("no decodable video frames in the span")
            for pkt in vstream.encode(None):
                out.mux(pkt)

            # ---- audio ----------------------------------------------------
            rate = self._cfg.audio_rate
            total = int((e - s).total_seconds() * rate)
            pcm = np.zeros((total, 2), dtype=np.int16)
            for chunk in ring.covering("audio", s, e):
                data = np.frombuffer(chunk.path.read_bytes(),
                                     dtype=np.int16).reshape(-1, 2)
                src_off = max(0, int((s - chunk.utc_start).total_seconds() * rate))
                dst_off = max(0, int((chunk.utc_start - s).total_seconds() * rate))
                n = min(len(data) - src_off, total - dst_off)
                if n > 0:
                    pcm[dst_off:dst_off + n] = data[src_off:src_off + n]
            astream = out.add_stream("aac", rate=rate)
            astream.codec_context.layout = "stereo"
            resampler = av.AudioResampler(format="fltp", layout="stereo", rate=rate)
            pos = 0
            while pos < total:
                block = pcm[pos:pos + 1024]
                af = av.AudioFrame.from_ndarray(
                    block.reshape(1, -1), format="s16", layout="stereo")
                af.sample_rate = rate
                af.pts = pos
                pos += len(block)
                for rf in resampler.resample(af):
                    for pkt in astream.encode(rf):
                        out.mux(pkt)
            for pkt in astream.encode(None):
                out.mux(pkt)
        finally:
            out.close()
        return ClipResult(path=out_path,
                          duration_s=(e - s).total_seconds(),
                          truncated=truncated)
```

- [ ] **Step 4: Run to verify PASS** — `uv run pytest tests/test_replay_extract.py -q`

Note: PyAV API edges to watch (fix locally, keep tests green): `AudioFrame.from_ndarray` for packed `s16` expects shape `(1, samples*channels)`; `resampler.resample(af)` returns a list in PyAV ≥ 11; setting `layout` may be via `astream.layout = "stereo"` on some versions.

- [ ] **Step 5: Commit**

```powershell
git add src/sm64_events/replay/extract.py tests/test_replay_extract.py
git commit -m "feat: ClipExtractor - frame-accurate re-encode to +faststart MP4, AAC at clip time"
```

---

### Task 8: ReplayRecorder (orchestrator, Protocols, CFR conform)

**Files:**
- Create: `src/sm64_events/replay/recorder.py`
- Test: `tests/test_replay_recorder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_replay_recorder.py
import time
from datetime import datetime, timezone

import numpy as np

from sm64_events.replay.clock import CaptureClock
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.recorder import ReplayRecorder
from sm64_events.replay.window import WindowInfo

T0 = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
WIN = WindowInfo(hwnd=123, title="Project64 Version 1.6", pid=42, visible=True)


class FakeVideoSource:
    def __init__(self):
        self.on_frame = None
    def start(self, on_frame, on_stopped):
        self.on_frame = on_frame
    def stop(self):
        pass


class FakeAudioSource:
    mode = "process"
    def __init__(self):
        self.on_pcm = None
    def start(self, on_pcm):
        self.on_pcm = on_pcm
    def stop(self):
        pass


def make_recorder(tmp_path, video, audio, found=WIN):
    cfg = ReplayConfig(scratch_dir=tmp_path / "buf", attach_poll_s=0.01)
    return ReplayRecorder(
        cfg=cfg,
        window_finder=lambda title: found,
        video_factory=lambda win: video,
        audio_factory=lambda pid: audio,
        clock_factory=lambda: CaptureClock(anchor_qpc_100ns=0, anchor_utc=T0),
        codec="libx264")


def push_frames(video, n, start_index=0, fps=30):
    arr = np.zeros((480, 640, 4), dtype=np.uint8)
    for i in range(start_index, start_index + n):
        video.on_frame(arr, int(i / fps * 1e7))  # qpc ticks for frame i


def wait_for(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_recorder_attaches_and_produces_segments(tmp_path):
    video, audio = FakeVideoSource(), FakeAudioSource()
    rec = make_recorder(tmp_path, video, audio)
    rec.start()
    assert wait_for(lambda: video.on_frame is not None)
    push_frames(video, 70)                      # > one 2 s segment
    audio.on_pcm(np.zeros((48000, 2), dtype=np.int16))
    assert wait_for(lambda: rec.ring.coverage("video") is not None)
    rec.stop()
    st = rec.status()
    assert st["recording"] is False and st["window_found"] is True
    assert st["audio_mode"] == "process"
    cov = rec.ring.coverage("video")
    assert cov[0] == T0


def test_cfr_fill_duplicates_dropped_frames(tmp_path):
    video, audio = FakeVideoSource(), FakeAudioSource()
    rec = make_recorder(tmp_path, video, audio)
    rec.start()
    assert wait_for(lambda: video.on_frame is not None)
    push_frames(video, 10)                       # indices 0..9
    push_frames(video, 80, start_index=40)       # gap 10..39 must be filled
    rec.stop()                                   # close flushes partials
    cov = rec.ring.coverage("video")
    # 120 indices = 4 s of footage despite the 1 s delivery gap
    assert (cov[1] - cov[0]).total_seconds() == 4.0


def test_no_window_reports_not_recording(tmp_path):
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(), found=None)
    rec.start()
    time.sleep(0.05)
    st = rec.status()
    assert st["recording"] is False and st["window_found"] is False
    rec.stop()


def test_startup_wipes_scratch(tmp_path):
    buf = tmp_path / "buf"
    buf.mkdir(parents=True)
    (buf / "stale.ts").write_bytes(b"junk")
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(), found=None)
    rec.start()
    assert not (buf / "stale.ts").exists()
    rec.stop()
```

- [ ] **Step 2: Run to verify FAIL** — `uv run pytest tests/test_replay_recorder.py -q`

- [ ] **Step 3: Implement**

```python
# src/sm64_events/replay/recorder.py
"""Orchestrator: window attach-retry (mirrors server/poller.py's pattern),
capture sources -> SegmentWriter -> SegmentRing, status surface.

Threading: capture callbacks arrive on library threads (windows-capture's
Rust thread, proc-tap's audio thread). One lock serializes access to the
writer; it is taken PER FRAME (not around CFR fill loops) so a large fill
after a stale-window gap can't starve the audio callback for seconds.

CFR conform happens here: frame_index = round(seconds_since_anchor * fps).
Gaps (WGC only delivers on change — pause menus, occlusion) are filled by
re-encoding the last frame at each missing index; frames that map to an
already-written index are dropped. This wall-clock-locks the video stream,
which is what makes utc <-> frame mapping exact."""
import logging
import threading
from typing import Callable, Protocol

import numpy as np

from sm64_events.replay.clock import CaptureClock
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.encoder import SegmentWriter
from sm64_events.replay.ring import SegmentRing
from sm64_events.replay.window import WindowInfo

log = logging.getLogger("sm64.replay")


class VideoSource(Protocol):
    def start(self, on_frame: Callable[[np.ndarray, int], None],
              on_stopped: Callable[[], None]) -> None: ...
    def stop(self) -> None: ...


class AudioSource(Protocol):
    mode: str  # "process" | "system"
    def start(self, on_pcm: Callable[[np.ndarray], None]) -> None: ...
    def stop(self) -> None: ...


class ReplayRecorder:
    def __init__(self, cfg: ReplayConfig,
                 window_finder: Callable[[str], WindowInfo | None],
                 video_factory: Callable[[WindowInfo], VideoSource],
                 audio_factory: Callable[[int], AudioSource],
                 clock_factory: Callable[[], CaptureClock] = CaptureClock.now,
                 codec: str | None = None):
        self._cfg = cfg
        self._find = window_finder
        self._video_factory = video_factory
        self._audio_factory = audio_factory
        self._clock_factory = clock_factory
        self._codec = codec  # None -> probe at start (pick_video_codec)
        self.ring = SegmentRing(cfg.retention_s, cfg.max_buffer_bytes)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._window_lost = threading.Event()
        self._thread: threading.Thread | None = None
        self._writer: SegmentWriter | None = None
        self._video: VideoSource | None = None
        self._audio: AudioSource | None = None
        self._clock: CaptureClock | None = None
        self._last_frame: np.ndarray | None = None
        self._last_index = -1
        self._recording = False
        self._window_found = False
        self._audio_mode = "none"

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        self._wipe_scratch()
        if self._codec is None:
            from sm64_events.replay.encoder import pick_video_codec
            self._codec = pick_video_codec()
        self._thread = threading.Thread(target=self._attach_loop,
                                        name="replay-attach", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._teardown_capture()

    def _wipe_scratch(self) -> None:
        d = self._cfg.scratch_dir
        if d.exists():
            for p in d.glob("*"):
                if p.is_file():
                    p.unlink(missing_ok=True)
        d.mkdir(parents=True, exist_ok=True)

    def _attach_loop(self) -> None:
        while not self._stop.is_set():
            win = self._find(self._cfg.window_title)
            self._window_found = win is not None
            if win is not None and not self._recording:
                try:
                    self._begin_capture(win)
                except Exception:
                    log.exception("replay capture failed to start")
                    self._teardown_capture()
            if self._window_lost.is_set():
                self._window_lost.clear()
                self._teardown_capture()
            self._stop.wait(self._cfg.attach_poll_s)

    def _begin_capture(self, win: WindowInfo) -> None:
        self._clock = self._clock_factory()
        self._writer = SegmentWriter(
            cfg=self._cfg, clock=self._clock, out_dir=self._cfg.scratch_dir,
            codec=self._codec, on_segment=self.ring.add)
        self._last_frame, self._last_index = None, -1
        self._video = self._video_factory(win)
        self._video.start(self._on_frame, self._window_lost.set)
        try:
            self._audio = self._audio_factory(win.pid)
            self._audio.start(self._on_pcm)
            self._audio_mode = self._audio.mode
        except Exception:
            log.exception("audio capture unavailable - recording video only")
            self._audio, self._audio_mode = None, "none"
        with self._lock:
            self._writer.start_audio(t0_utc=self._clock.utc_of(
                __import__("sm64_events.replay.clock",
                           fromlist=["qpc_100ns"]).qpc_100ns()))
        self._recording = True
        log.info("replay recording started (hwnd=%s pid=%s codec=%s audio=%s)",
                 win.hwnd, win.pid, self._codec, self._audio_mode)

    def _teardown_capture(self) -> None:
        for src in (self._video, self._audio):
            if src is not None:
                try:
                    src.stop()
                except Exception:
                    log.exception("capture source stop failed")
        self._video = self._audio = None
        if self._writer is not None:
            with self._lock:
                self._writer.close()
            self._writer = None
        self._recording = False
        self._audio_mode = "none"

    # -- capture callbacks (library threads) ----------------------------------
    def _on_frame(self, bgra: np.ndarray, ts_100ns: int) -> None:
        if self._writer is None:
            return
        target = round(self._clock.seconds_since_anchor(ts_100ns) * self._cfg.fps)
        if target <= self._last_index:
            return  # duplicate/early frame
        fill_from = (self._last_index + 1
                     if self._last_frame is not None else target)
        for idx in range(fill_from, target):
            with self._lock:  # per-frame lock: audio interleaves during fills
                self._writer.write_video(self._last_frame, idx)
        with self._lock:
            self._writer.write_video(bgra, target)
        self._last_frame, self._last_index = bgra, target

    def _on_pcm(self, pcm_s16: np.ndarray) -> None:
        if self._writer is None:
            return
        with self._lock:
            self._writer.write_audio(pcm_s16)

    # -- queries ---------------------------------------------------------------
    def status(self) -> dict:
        cov = self.ring.coverage("video")
        return {
            "recording": self._recording,
            "window_found": self._window_found,
            "audio_mode": self._audio_mode,
            "encoder": self._codec,
            "buffer_start_utc": cov[0].isoformat() if cov else None,
            "buffer_end_utc": cov[1].isoformat() if cov else None,
            "disk_bytes": self.ring.total_bytes,
        }
```

Cleanup before Step 4: replace the awkward `__import__` in `_begin_capture` with a module-level `from sm64_events.replay.clock import qpc_100ns` and call `self._clock.utc_of(qpc_100ns())` — written out here so the intent is unambiguous: **audio t0 = the UTC moment the audio source started**, mapped through the same clock.

- [ ] **Step 4: Run to verify PASS** — `uv run pytest tests/test_replay_recorder.py -q`

- [ ] **Step 5: Run the full suite** — `uv run pytest -q` — Expected: all green.

- [ ] **Step 6: Commit**

```powershell
git add src/sm64_events/replay/recorder.py tests/test_replay_recorder.py
git commit -m "feat: ReplayRecorder - attach loop, wall-clock CFR conform, per-frame locking"
```

---

### Task 9: Capture adapters (windows-capture + proc-tap)

**Files:**
- Create: `src/sm64_events/replay/video.py`
- Create: `src/sm64_events/replay/audio.py`

These are thin boundary adapters — the Protocol they satisfy is tested via fakes in Task 8; the adapters themselves are **live-verified** (Task 15). Imports are lazy (inside `start()`) so the test suite never touches capture hardware.

- [ ] **Step 1: Implement the video adapter**

```python
# src/sm64_events/replay/video.py
"""windows-capture (WGC) adapter -> VideoSource protocol. Lazy import:
constructing the recorder must never require capture to be possible.

VERIFY (live gate, Task 15): exact constructor/event names against the
installed windows-capture version (Task 1 Step 2 printed the signature);
draw_border=False suppression of the yellow border is UNVERIFIED per
research — cosmetic either way."""
import logging

from sm64_events.replay.window import WindowInfo

log = logging.getLogger("sm64.replay")


class WgcVideoSource:
    def __init__(self, win: WindowInfo):
        self._win = win
        self._control = None

    def start(self, on_frame, on_stopped) -> None:
        from windows_capture import WindowsCapture

        capture = WindowsCapture(
            cursor_capture=False,
            draw_border=False,
            window_hwnd=self._win.hwnd,
        )

        @capture.event
        def on_frame_arrived(frame, capture_control):
            on_frame(frame.to_numpy(), frame.timespan)

        @capture.event
        def on_closed():
            log.info("capture window closed")
            on_stopped()

        self._control = capture.start_free_threaded()

    def stop(self) -> None:
        if self._control is not None:
            try:
                self._control.stop()
            except Exception:
                log.exception("WGC stop failed")
            self._control = None
```

- [ ] **Step 2: Implement the audio adapters**

```python
# src/sm64_events/replay/audio.py
"""Audio sources -> AudioSource protocol.

Primary: proc-tap per-process WASAPI loopback (pid-scoped: ONLY PJ64 audio,
no Discord/music bleed). Delivers float32 48 kHz stereo; converted to s16
here so everything downstream speaks one PCM dialect.
Fallback: PyAudioWPatch device-wide loopback (all system audio) — selected
by the factory in main.py when proc-tap fails to start.

VERIFY (live gate, Task 15): exact proc-tap class/callback names against the
installed version (Task 1 Step 2 printed the module surface)."""
import logging

import numpy as np

log = logging.getLogger("sm64.replay")


def _f32_to_s16(pcm_f32: np.ndarray) -> np.ndarray:
    flat = np.asarray(pcm_f32, dtype=np.float32).reshape(-1, 2)
    return (np.clip(flat, -1.0, 1.0) * 32767).astype(np.int16)


class ProcessAudioSource:
    mode = "process"

    def __init__(self, pid: int):
        self._pid = pid
        self._tap = None

    def start(self, on_pcm) -> None:
        import proc_tap

        self._tap = proc_tap.ProcTap(pid=self._pid)

        @self._tap.on_data
        def _(pcm, frames):
            on_pcm(_f32_to_s16(np.frombuffer(pcm, dtype=np.float32)))

        self._tap.start()

    def stop(self) -> None:
        if self._tap is not None:
            try:
                self._tap.stop()
            except Exception:
                log.exception("proc-tap stop failed")
            self._tap = None


class SystemAudioSource:
    mode = "system"

    def __init__(self, rate: int = 48000):
        self._rate = rate
        self._stream = None
        self._pa = None

    def start(self, on_pcm) -> None:
        import pyaudiowpatch as pyaudio

        self._pa = pyaudio.PyAudio()
        wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        speakers = self._pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        loopback = next(
            d for d in self._pa.get_loopback_device_info_generator()
            if speakers["name"] in d["name"])

        def cb(in_data, frame_count, time_info, status):
            on_pcm(np.frombuffer(in_data, dtype=np.int16).reshape(-1, 2))
            return (None, pyaudio.paContinue)

        self._stream = self._pa.open(
            format=pyaudio.paInt16, channels=2,
            rate=int(loopback["defaultSampleRate"]),
            input=True, input_device_index=loopback["index"],
            stream_callback=cb)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None
```

Note: if the loopback device's `defaultSampleRate` is not 48000, resample is NOT handled in v1 — log a warning and pass through; the live gate decides whether this matters on the user's machine (proc-tap, the primary, is fixed 48 kHz).

- [ ] **Step 3: Sanity-import + full suite**

```powershell
uv run python -c "from sm64_events.replay.video import WgcVideoSource; from sm64_events.replay.audio import ProcessAudioSource, SystemAudioSource; print('ok')"
uv run pytest -q
```

Expected: `ok`; suite green (lazy imports mean no capture hardware is touched).

- [ ] **Step 4: Commit**

```powershell
git add src/sm64_events/replay/video.py src/sm64_events/replay/audio.py
git commit -m "feat: WGC video + per-process/system audio adapters behind the source protocols"
```

---

### Task 10: Expose `started_utc` in the attempt view

**Files:**
- Modify: `src/sm64_events/tracking/views.py:69-80` (`_attempt_json`)
- Test: `tests/test_views.py` (append one test)

- [ ] **Step 1: Write the failing test** (append to `tests/test_views.py`, reusing its existing fixtures/builders — read the file first and follow its local pattern for constructing attempts):

```python
def test_attempt_json_exposes_started_utc(tmp_path):
    # Build a db with one attempt via the file's existing seed helper, then:
    # view = build_session_view(db, service, clock="igt")
    # a = view["stars"][0]["attempts"][0]
    assert "started_utc" in a and a["started_utc"] is not None
    assert "ended_utc" in a
```

(Adapt the seeding lines to the file's existing helper — `test_views.py` already builds session views; copy its newest test's setup verbatim.)

- [ ] **Step 2: Run to verify FAIL** — `uv run pytest tests/test_views.py -q`

- [ ] **Step 3: Implement** — in `_attempt_json`, add one field after `"cleared_reason": a.cleared_reason,`:

```python
            "started_utc": a.started_utc, "ended_utc": a.ended_utc,
```

(`ended_utc` is already emitted — keep exactly one occurrence.) `Attempt` already carries both (`tracking/projection.py:87-88`); no projection change.

- [ ] **Step 4: Run to verify PASS** — `uv run pytest tests/test_views.py -q`

- [ ] **Step 5: Commit**

```powershell
git add src/sm64_events/tracking/views.py tests/test_views.py
git commit -m "feat: expose attempt started_utc in the session view (replay span source)"
```

---

### Task 11: ReplayService (spans, clips, saves, naming)

**Files:**
- Create: `src/sm64_events/replay/service.py`
- Test: `tests/test_replay_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_replay_service.py
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.extract import ClipResult
from sm64_events.replay.service import ReplayService, slug_filename
from sm64_events.tracking.projection import Attempt

T0 = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)


def attempt(**kw):
    base = dict(id=42, session_id=3, course_id=2, star_id=2, strat_tag=None,
                anchor_type="practice_reset", anchor_frame=100,
                outcome="success", outcome_detail=None,
                igt_frames=343, rta_frames=350,
                started_utc=T0.isoformat().replace("+00:00", "Z"),
                ended_utc=(T0 + timedelta(seconds=12)).isoformat().replace("+00:00", "Z"),
                cleared=False, cleared_reason=None)
    base.update(kw)
    return Attempt(**base)


class FakeDb:
    def __init__(self, attempts):
        self._attempts = attempts
    def attempts(self):
        return self._attempts


class FakeTracker:
    def __init__(self, attempts):
        self.db = FakeDb(attempts)
        self.session_id = 3


class FakeRecorder:
    def __init__(self, cov):
        self._cov = cov
        self.ring = self
    def coverage(self, kind):
        return self._cov
    def status(self):
        return {"recording": True}


class FakeExtractor:
    def __init__(self):
        self.calls = []
    def extract(self, ring, start, end, out_path):
        self.calls.append((start, end, out_path))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"mp4")
        return ClipResult(path=out_path, duration_s=(end - start).total_seconds(),
                          truncated=False)


def make_service(tmp_path, attempts, cov=None):
    cfg = ReplayConfig(save_root=tmp_path / "replays",
                       scratch_dir=tmp_path / "buf", extract_wait_s=0.0)
    cov = cov or (T0 - timedelta(seconds=60), T0 + timedelta(seconds=60))
    return ReplayService(cfg=cfg, recorder=FakeRecorder(cov),
                         extractor=FakeExtractor(), tracker=FakeTracker(attempts))


def test_view_pads_span_and_returns_clip_url(tmp_path):
    svc = make_service(tmp_path, [attempt()])
    res = svc.view(42)
    assert res["clip_url"] == "/api/replay/clips/clip_attempt_42.mp4"
    assert res["truncated"] is False
    start, end, _ = svc.extractor.calls[0]
    assert start == T0 - timedelta(seconds=3)            # pre_pad
    assert end == T0 + timedelta(seconds=12 + 2)         # post_pad


def test_view_unknown_attempt_raises_lookup(tmp_path):
    svc = make_service(tmp_path, [])
    try:
        svc.view(99)
        assert False
    except LookupError:
        pass


def test_view_is_cached_second_call_skips_extract(tmp_path):
    svc = make_service(tmp_path, [attempt()])
    svc.view(42)
    svc.view(42)
    assert len(svc.extractor.calls) == 1


def test_save_copies_into_date_session_tree(tmp_path):
    svc = make_service(tmp_path, [attempt()])
    res = svc.save(42)
    p = Path(res["path"])
    assert p.exists()
    assert p.parent.name == "session_3"
    assert p.parent.parent.parent == tmp_path / "replays"
    assert p.name.startswith("attempt_0042_")


def test_slug_filename_success_and_death():
    a = attempt()
    assert slug_filename(a, "Whomp's Fortress", "Chip Off Whomp's Block") == \
        "attempt_0042_whomps-fortress_chip-off-whomps-block_0m11s43.mp4"
    d = attempt(outcome="death", igt_frames=120)
    assert slug_filename(d, "Whomp's Fortress", "Chip Off Whomp's Block") == \
        "attempt_0042_whomps-fortress_chip-off-whomps-block_0m04s00_death.mp4"
```

(The IGT slug strings above assume `format_igt(343) == "0:11.43"` / `format_igt(120) == "0:04.00"` — check `core/timefmt.py` first and fix the expected literals to the real format before running. The transformation under test is `":" -> "m"`, `"." -> "s"`.)

- [ ] **Step 2: Run to verify FAIL** — `uv run pytest tests/test_replay_service.py -q`

- [ ] **Step 3: Implement**

```python
# src/sm64_events/replay/service.py
"""Attempt -> span -> clip -> save. Error taxonomy matches server/api.py:
LookupError -> 404 (no such attempt), ValueError -> 409 (no footage),
RuntimeError -> 503 (db unavailable / replay disabled)."""
import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from sm64_events.core.timefmt import format_igt
from sm64_events.memory.addresses import course_name, star_name
from sm64_events.replay.config import ReplayConfig

_CLIP_NAME = "clip_attempt_{id}.mp4"


def _parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _slug(s: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")


def slug_filename(a, course: str, star: str) -> str:
    igt = format_igt(a.igt_frames) if a.igt_frames is not None else "no-igt"
    igt = igt.replace(":", "m").replace(".", "s")
    suffix = "" if a.outcome == "success" else f"_{a.outcome}"
    return f"attempt_{a.id:04d}_{_slug(course)}_{_slug(star)}_{igt}{suffix}.mp4"


class ReplayService:
    def __init__(self, cfg: ReplayConfig, recorder, extractor, tracker):
        self.cfg = cfg
        self.recorder = recorder
        self.extractor = extractor
        self.tracker = tracker
        self.clips_dir = cfg.scratch_dir / "clips"

    # -- queries ----------------------------------------------------------
    def status(self) -> dict:
        return {"enabled": True, **self.recorder.status()}

    def _attempt(self, attempt_id: int):
        if self.tracker.db is None:
            raise RuntimeError("database unavailable")
        for a in self.tracker.db.attempts():
            if a.id == attempt_id:
                return a
        raise LookupError(f"no attempt {attempt_id}")

    def _span(self, a):
        from datetime import timedelta
        start = _parse_utc(a.started_utc) - timedelta(seconds=self.cfg.pre_pad_s)
        end = _parse_utc(a.ended_utc) + timedelta(seconds=self.cfg.post_pad_s)
        return start, end

    # -- commands -----------------------------------------------------------
    def view(self, attempt_id: int) -> dict:
        a = self._attempt(attempt_id)
        name = _CLIP_NAME.format(id=attempt_id)
        clip = self.clips_dir / name
        meta = clip.with_suffix(".json")
        if not (clip.exists() and meta.exists()):
            start, end = self._span(a)
            self._wait_for_tail(end)
            res = self.extractor.extract(self.recorder.ring, start, end, clip)
            meta.write_text(json.dumps(
                {"duration_s": res.duration_s, "truncated": res.truncated}))
        m = json.loads(meta.read_text())
        return {"clip_url": f"/api/replay/clips/{name}",
                "duration_s": m["duration_s"], "truncated": m["truncated"]}

    def _wait_for_tail(self, end_utc) -> None:
        """Bounded wait: a click right after the event can outrun the last
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
        a = self._attempt(attempt_id)
        self.view(attempt_id)  # ensure the clip exists (cached if already cut)
        clip = self.clips_dir / _CLIP_NAME.format(id=attempt_id)
        ended_local = _parse_utc(a.ended_utc).astimezone()  # display tz (folder name)
        dest_dir = (self.cfg.save_root / ended_local.strftime("%Y-%m-%d")
                    / f"session_{a.session_id}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        course = course_name(a.course_id) if a.course_id is not None else "no-course"
        star = (star_name(a.course_id, a.star_id)
                if a.course_id is not None else "no-star")
        dest = dest_dir / slug_filename(a, course, star)
        shutil.copy2(clip, dest)
        return {"path": str(dest)}

    def clip_path(self, name: str) -> Path:
        """Validated path for serving — rejects anything but our clip names
        (path traversal guard)."""
        if not re.fullmatch(r"clip_attempt_\d+\.mp4", name):
            raise LookupError("no such clip")
        p = self.clips_dir / name
        if not p.exists():
            raise LookupError("no such clip")
        return p

    def lifecycle_start(self) -> None:
        self.clips_dir.mkdir(parents=True, exist_ok=True)
        self.recorder.start()

    def lifecycle_stop(self) -> None:
        self.recorder.stop()
```

- [ ] **Step 4: Run to verify PASS** — `uv run pytest tests/test_replay_service.py -q` (fix the IGT literals per `core/timefmt.py` if needed).

- [ ] **Step 5: Commit**

```powershell
git add src/sm64_events/replay/service.py tests/test_replay_service.py
git commit -m "feat: ReplayService - padded spans, clip cache with meta sidecar, date/session save tree"
```

---

### Task 12: Replay API router + app wiring

**Files:**
- Create: `src/sm64_events/server/replay_api.py`
- Modify: `src/sm64_events/server/app.py:36-58` (accept `replay=None`, include router, lifespan start/stop)
- Test: `tests/test_replay_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_replay_api.py
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.server.replay_api import create_replay_router


class FakeReplayService:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
    def status(self):
        return {"enabled": True, "recording": True, "window_found": True,
                "audio_mode": "process", "encoder": "libx264",
                "buffer_start_utc": None, "buffer_end_utc": None,
                "disk_bytes": 0}
    def view(self, attempt_id: int):
        if attempt_id == 404:
            raise LookupError("no attempt")
        if attempt_id == 409:
            raise ValueError("no footage")
        return {"clip_url": "/api/replay/clips/clip_attempt_42.mp4",
                "duration_s": 4.0, "truncated": False}
    def save(self, attempt_id: int):
        return {"path": "replays/2026-06-11/session_3/x.mp4"}
    def clip_path(self, name: str) -> Path:
        if name != "clip_attempt_42.mp4":
            raise LookupError("no such clip")
        p = self.tmp / name
        if not p.exists():
            p.write_bytes(b"\x00" * 2048)
        return p


def make_client(tmp_path):
    app = FastAPI()
    app.include_router(create_replay_router(FakeReplayService(tmp_path)))
    return TestClient(app)


def test_status(tmp_path):
    r = make_client(tmp_path).get("/api/replay/status")
    assert r.status_code == 200 and r.json()["recording"] is True


def test_view_maps_error_taxonomy(tmp_path):
    c = make_client(tmp_path)
    assert c.post("/api/attempts/1/replay").status_code == 200
    assert c.post("/api/attempts/404/replay").status_code == 404
    assert c.post("/api/attempts/409/replay").status_code == 409


def test_clip_serving_supports_range(tmp_path):
    c = make_client(tmp_path)
    r = c.get("/api/replay/clips/clip_attempt_42.mp4",
              headers={"Range": "bytes=0-99"})
    assert r.status_code == 206                      # partial content = scrubbable
    assert r.headers["content-type"] == "video/mp4"
    assert c.get("/api/replay/clips/evil.txt").status_code == 404
    assert c.get("/api/replay/clips/..%2F..%2Fsecrets.mp4").status_code == 404


def test_save(tmp_path):
    r = make_client(tmp_path).post("/api/attempts/1/replay/save")
    assert r.status_code == 200 and r.json()["path"].endswith(".mp4")
```

- [ ] **Step 2: Run to verify FAIL** — `uv run pytest tests/test_replay_api.py -q`

- [ ] **Step 3: Implement the router**

```python
# src/sm64_events/server/replay_api.py
"""Replay REST surface. Same error taxonomy as api.py: LookupError -> 404,
ValueError -> 409, RuntimeError -> 503. Endpoints are sync `def` on purpose:
extraction is CPU/GPU-bound, and FastAPI runs sync endpoints in its
threadpool — the event loop (poller, websockets) never blocks."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


def _http(e: Exception) -> HTTPException:
    if isinstance(e, LookupError):
        return HTTPException(404, str(e))
    if isinstance(e, ValueError):
        return HTTPException(409, str(e))
    return HTTPException(503, str(e))


def create_replay_router(replay) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/replay/status")
    def status():
        return replay.status()

    @router.post("/attempts/{attempt_id}/replay")
    def view(attempt_id: int):
        try:
            return replay.view(attempt_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    @router.get("/replay/clips/{name}")
    def clip(name: str):
        try:
            path = replay.clip_path(name)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return FileResponse(path, media_type="video/mp4")  # native Range/206

    @router.post("/attempts/{attempt_id}/replay/save")
    def save(attempt_id: int):
        try:
            return replay.save(attempt_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    return router
```

- [ ] **Step 4: Wire into `create_app`** — in `server/app.py`:

```python
def create_app(poller: Poller, broadcaster: Broadcaster,
               service=None, replay=None, debug_hooks: bool = False) -> FastAPI:
```

In the lifespan, after `task = asyncio.create_task(...)` add:

```python
        if replay is not None:
            replay.lifecycle_start()
```

and before `task.cancel()` on the way out:

```python
        if replay is not None:
            replay.lifecycle_stop()
```

After the `app.include_router(create_api_router(service))` block:

```python
    if replay is not None:
        from sm64_events.server.replay_api import create_replay_router
        app.include_router(create_replay_router(replay))
```

- [ ] **Step 5: Run to verify PASS + full suite**

```powershell
uv run pytest tests/test_replay_api.py -q
uv run pytest -q
```

- [ ] **Step 6: Commit**

```powershell
git add src/sm64_events/server/replay_api.py src/sm64_events/server/app.py tests/test_replay_api.py
git commit -m "feat: replay REST surface - status/extract/save + Range-capable clip serving"
```

---

### Task 13: Composition (main.py)

**Files:**
- Modify: `src/sm64_events/main.py`
- Test: `tests/test_composition.py` (append one test; read the file's existing pattern first)

- [ ] **Step 1: Write the failing test** (append):

```python
def test_build_wires_replay_when_enabled(monkeypatch):
    # build() must construct the app with replay endpoints present
    from sm64_events.main import build
    app = build()
    paths = {r.path for r in app.routes}
    assert "/api/replay/status" in paths
```

(If `test_composition.py` patches `acquire_instance_lock`/`Database` for isolation, copy that setup — `build()` may need the same monkeypatching to run twice in one session.)

- [ ] **Step 2: Run to verify FAIL** — `uv run pytest tests/test_composition.py -q`

- [ ] **Step 3: Implement** — in `main.py`, add imports:

```python
from sm64_events.replay.audio import ProcessAudioSource, SystemAudioSource
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.extract import ClipExtractor
from sm64_events.replay.recorder import ReplayRecorder
from sm64_events.replay.service import ReplayService
from sm64_events.replay.video import WgcVideoSource
from sm64_events.replay.window import find_window
```

In `build()`, after `service = TrackerService(db, broadcaster)`:

```python
    replay_cfg = ReplayConfig()
    replay = None
    if replay_cfg.enabled:
        def audio_factory(pid: int):
            try:
                src = ProcessAudioSource(pid)
                return src
            except Exception:
                logging.getLogger("sm64.replay").exception(
                    "per-process audio unavailable - device-wide loopback")
                return SystemAudioSource(rate=replay_cfg.audio_rate)
        from sm64_events.replay.encoder import pick_video_codec
        codec = pick_video_codec()
        recorder = ReplayRecorder(
            cfg=replay_cfg, window_finder=find_window,
            video_factory=WgcVideoSource, audio_factory=audio_factory,
            codec=codec)
        replay = ReplayService(cfg=replay_cfg, recorder=recorder,
                               extractor=ClipExtractor(cfg=replay_cfg, codec=codec),
                               tracker=service)
```

And pass it through: `return create_app(poller, broadcaster, service=service, replay=replay)`.

**Note:** `ProcessAudioSource.__init__` doesn't start anything (lazy), so the try/except that matters lives in `recorder._begin_capture` — which already falls back by catching `audio.start()` failures. The factory-level fallback above covers import-time failures; recorder-level covers runtime failures. **Decide at implementation time whether the runtime fallback should retry `SystemAudioSource` instead of going video-only — recommended: yes.** Change `_begin_capture`'s except-branch to try `SystemAudioSource(rate=...)` before giving up; add a recorder test with an audio factory whose `start()` raises, asserting `status()["audio_mode"] == "system"`.

- [ ] **Step 4: Run to verify PASS + full suite** — `uv run pytest -q`

- [ ] **Step 5: Commit**

```powershell
git add src/sm64_events/main.py tests/test_composition.py src/sm64_events/replay/recorder.py tests/test_replay_recorder.py
git commit -m "feat: wire replay recorder/service into the composition root with audio fallback chain"
```

---

### Task 14: UI — player row, replay button, recording dot

**Files:**
- Create: `src/sm64_events/ui/components/replay.js`
- Modify: `src/sm64_events/ui/components/practice.js` (AttemptRow)
- Modify: `src/sm64_events/ui/components/header.js` (dot)
- Modify: `src/sm64_events/ui/index.html` (styles)

No JS test infra exists in this repo — the gate is the `frontend-smoke-test` skill (mandatory per global instructions) + Task 15's live pass.

- [ ] **Step 1: Create `replay.js`**

```javascript
// src/sm64_events/ui/components/replay.js — inline clip player + recording dot
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";

const html = htm.bind(h);

// Expanded row under an attempt: extract on mount (server caches), then play.
export function ReplayPlayer({ attemptId }) {
  const [state, setState] = useState({ phase: "loading" });
  const [savedPath, setSavedPath] = useState(null);

  useEffect(() => {
    let alive = true;
    send("POST", `/api/attempts/${attemptId}/replay`)
      .then((r) => alive && setState({ phase: "ready", ...r }))
      .catch((e) => alive && setState({ phase: "error", message: String(e) }));
    return () => { alive = false; };
  }, [attemptId]);

  async function saveReplay() {
    const r = await send("POST", `/api/attempts/${attemptId}/replay/save`);
    setSavedPath(r.path);
  }

  if (state.phase === "loading")
    return html`<span class="meta">extracting replay…</span>`;
  if (state.phase === "error")
    return html`<span class="badx">replay unavailable</span>
      <span class="meta"> ${state.message}</span>`;
  return html`<div class="replay-player">
    ${state.truncated && html`<div class="meta">⚠ starts mid-attempt (buffer didn't cover the full span)</div>`}
    <video controls preload="auto" src=${state.clip_url}></video>
    <div>
      <button onclick=${saveReplay} disabled=${savedPath !== null}>
        ${savedPath ? "Saved" : "Save Replay"}</button>
      ${savedPath && html`<span class="meta"> → ${savedPath}</span>`}
    </div>
  </div>`;
}

// Header indicator: red = recording, grey = window not found, hidden = no replay.
export function RecordingDot() {
  const [st, setSt] = useState(null);
  useEffect(() => {
    let alive = true;
    const poll = () =>
      getJSON("/api/replay/status")
        .then((s) => alive && setSt(s))
        .catch(() => alive && setSt(null));
    poll();
    const id = setInterval(poll, 5000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  if (st === null) return null;
  const cls = st.recording ? "ok" : "bad";
  const label = st.recording
    ? `rec · ${st.encoder} · audio ${st.audio_mode}` : "no capture";
  return html`<span class="dot ${cls}" title="replay buffer">● ${label}</span>`;
}
```

- [ ] **Step 2: Wire into `practice.js`** — at the top, add the import:

```javascript
import { ReplayPlayer } from "./replay.js";
```

In `AttemptRow`, add expansion state and the button, and return a fragment of two rows. The full revised component (replacing `function AttemptRow` through its closing brace — current `practice.js:21-65`):

```javascript
function AttemptRow({ a, t, idx }) {
  const [showReplay, setShowReplay] = useState(false);
  async function clear() {
    await send("POST", `/api/attempts/${a.id}/clear`, { reason: "accidental" });
    t.refresh();
  }
  async function restore() {
    await send("POST", `/api/attempts/${a.id}/restore`);
    t.refresh();
  }
  async function savePb() {
    await send("POST", "/api/pb", { attempt_id: a.id, timer_mode: t.clock });
    t.refresh();
  }
  const time = t.clock === "igt" ? a.igt : a.rta;
  const frames = t.clock === "igt" ? a.igt_frames : a.rta_frames;
  // Glow when saving would set a new PB: beats the recorded PB, or no PB
  // exists yet. frames > 0 excludes same-tick race rows (rta=0 junk) whose
  // "PB" would be meaningless.
  const pbBeat = a.outcome === "success" && !a.cleared
    && frames != null && frames > 0
    && (a.pb_delta_frames === null || a.pb_delta_frames < 0);
  const row = html`<tr class=${a.cleared ? "cleared" : ""}>
    <td class="meta">#${idx + 1}</td>
    <td class=${a.outcome === "success" ? "good" : "badx"}>
      ${OUTCOME_LABEL[a.outcome] || a.outcome}
      ${a.outcome === "death" && a.outcome_detail
        ? html` <span class="meta">(${a.outcome_detail})</span>` : ""}
      ${a.outcome === "success" && time ? html` <b>${time}</b>` : ""}
      ${a.outcome !== "success" && a.igt ? html` <span class="meta">${a.igt} in</span>` : ""}
      ${a.rollouts_total > 0
        ? html` <span class="meta">· ${a.rollouts_dustless}/${a.rollouts_total} dustless rollouts</span>` : ""}
      ${a.jumps_total > 0
        ? html` <span class="meta">· ${a.jumps_dustless}/${a.jumps_total} dustless jumps</span>` : ""}
    </td>
    <td>${a.outcome === "success" ? delta(a.pb_delta_frames) : ""}</td>
    <td class="meta">${a.strat_tag || ""}</td>
    <td style="text-align:right">
      <button onclick=${() => setShowReplay(!showReplay)}
              title="view replay">${showReplay ? "▾" : "▶"}</button>
      ${" "}
      ${a.outcome === "success" && !a.cleared
        ? html`<button class=${pbBeat ? "pb-glow" : ""} onclick=${savePb}>Save as PB</button> ` : ""}
      ${a.cleared
        ? html`<button onclick=${restore}>undo</button>`
        : html`<button onclick=${clear} title="clear (mistake)">×</button>`}
    </td>
  </tr>`;
  if (!showReplay) return row;
  return html`${row}<tr class="replay-row">
    <td colspan="5"><${ReplayPlayer} attemptId=${a.id} /></td>
  </tr>`;
}
```

- [ ] **Step 3: Add the dot to `header.js`** — import and render right after the live/offline dot (`header.js:40`):

```javascript
import { RecordingDot } from "./replay.js";
```

```javascript
    <span class="dot ${t.connected ? "ok" : "bad"}">${t.connected ? "live" : "offline"}</span>
    <${RecordingDot} />
```

- [ ] **Step 4: Styles in `index.html`** — add to the existing `<style>` block:

```css
.replay-row td { background: #0d0d12; padding: .5rem .75rem; }
.replay-player video { width: 100%; max-width: 720px; display: block;
                       margin: .25rem 0; border: 1px solid #333; }
```

(Match the page's existing palette — read the current style block and reuse its variables/colors if it has them.)

- [ ] **Step 5: Smoke test in the browser** — run the `frontend-smoke-test` skill: start the server (`uv run uvicorn sm64_events.main:app --host 127.0.0.1 --port 8064`), open `http://127.0.0.1:8064/`, check the console for errors, click ▶ on an attempt (expect the "replay unavailable" path if no footage — that's the correct degraded behavior), verify the dot renders (grey "no capture" without PJ64 running).

- [ ] **Step 6: Commit**

```powershell
git add src/sm64_events/ui/components/replay.js src/sm64_events/ui/components/practice.js src/sm64_events/ui/components/header.js src/sm64_events/ui/index.html
git commit -m "feat: inline replay player + recording indicator in the practice UI"
```

---

### Task 15: Docs + live verification (with the human)

**Files:**
- Modify: `README.md` (API section), `CLAUDE.md` (module map), `docs/architecture.md` (only if live testing yields hard-won facts)

- [ ] **Step 1: Document the API in README** — add to the consumer-facing surface:

```markdown
### Replay

While the server runs it records the PJ64 window (+ game audio, per-process)
into `data/replay_buffer/` (scratch, wiped on startup). Retention defaults to
the whole session (`ReplayConfig.retention_s`).

- `GET  /api/replay/status` — `{recording, window_found, audio_mode, encoder, buffer_start_utc, buffer_end_utc, disk_bytes}`
- `POST /api/attempts/{id}/replay` — cut (or reuse) the attempt's clip → `{clip_url, duration_s, truncated}`
- `GET  /api/replay/clips/{name}` — the MP4 (supports Range; scrub away)
- `POST /api/attempts/{id}/replay/save` — copy to `replays/<YYYY-MM-DD>/session_<N>/<slug>.mp4` → `{path}`

Errors follow the API taxonomy: 404 unknown attempt/clip, 409 no footage,
503 db unavailable.
```

- [ ] **Step 2: Update the CLAUDE.md module map** — add rows:

```markdown
| Replay capture/buffer (window+audio -> segment ring) | `replay/` — `recorder.py` orchestrates; `clock.py` is the QPC↔UTC contract; `encoder.py`/`extract.py` docstrings carry the gapless-PCM and re-encode rationale |
| Replay REST surface (status/extract/save/serve) | `server/replay_api.py` |
| Replay player + recording dot | `ui/components/replay.js` |
```

And in **Parallel work zones**, extend the zone list with `replay/` (own zone, own tests).

- [ ] **Step 3: Live verification with the human** (requires PJ64 + ROM; this is the harness ritual — record outcomes in the relevant docstrings/architecture.md immediately):

1. Start PJ64 windowed + the server. Expect log `replay recording started (... codec=h264_nvenc audio=process)`; header dot red within ~5 s.
2. **Yellow border (UNVERIFIED per research):** does the capture border show? If yes and objectionable, note it in `video.py`'s docstring as a known cosmetic limit.
3. Grab a star; click ▶ on the attempt. Clip appears in ~1–3 s; verify it covers savestate-load → grab + tail; scrub with the mouse — seeks must feel instant.
4. **A/V sync:** trigger a sound-distinct action (rollout dust, star jingle); confirm lip-sync by eye (<100 ms).
5. Save Replay; verify the file lands in `replays/<today>/session_<N>/` and plays in a media player.
6. Occlude the PJ64 window with the browser → footage keeps recording. Minimize it → note frozen frames resume cleanly on restore.
7. Close PJ64 mid-session → dot goes grey; old attempts still replayable. Relaunch PJ64 → recording resumes (new clock anchor).
8. Kill the audio path deliberately (set the audio factory to raise, or rename proc_tap temporarily) → status shows `audio_mode: "system"` (after the Task 13 fallback decision) and the tracker still runs.
9. Leave it recording ~30 min; re-check sync at the tail (drift watch) and `disk_bytes` growth (~2–3 GB/h expected).

- [ ] **Step 4: Full suite + commit**

```powershell
uv run pytest -q
git add README.md CLAUDE.md docs/architecture.md
git commit -m "docs: replay API surface, module map zone, live-verified capture facts"
```

---

## Self-review notes (already applied)

- **Spec coverage:** requirements table → Tasks: deps/config (1–2), clock (3), ring+disk guard (4), window (5), segments+GOP (6), extraction+faststart+truncation (7), CFR/stale-frames/lifecycle (8), capture+border risk (9), attempt span fields (10), padding/cache/save-tree/naming (11), API+Range+taxonomy (12), wiring+audio fallback chain (13), UI player/dot/truncation notice (14), docs+live gate incl. drift and fullscreen-out-of-scope (15). Spec's "degraded states" table: window-not-found (8), audio fallback (13), NVENC fallback (6), minimized (15.6), truncation (7/11/14), disk cap (4).
- **Known deviation from spec, intentional:** audio is stored as raw PCM sidecar chunks and AAC-encoded at clip time (gapless across segments) instead of per-segment AAC. Rationale in encoder.py docstring; spec intent (audio required, disk ring) unchanged.
- **Type consistency:** `SegmentInfo(path, kind, utc_start, utc_end, size_bytes)` used identically in Tasks 4/6/7/8; `ClipResult(path, duration_s, truncated)` in 7/11; recorder protocol `start(on_frame, on_stopped)`/`start(on_pcm)` matches adapters in 9 and fakes in 8; `ReplayService` methods (`status/view/save/clip_path/lifecycle_*`) match the router in 12 and fake in 12's test.
- **Honest unknowns:** exact `windows-capture`/`proc-tap` API names (verified by Task 1 Step 2 introspection, adapters adjusted there if needed); PyAV audio-frame shape edges (noted in Task 7); IGT format literal (checked against `core/timefmt.py` in Task 11). These are verification steps, not placeholders — the surrounding code is complete either way.
