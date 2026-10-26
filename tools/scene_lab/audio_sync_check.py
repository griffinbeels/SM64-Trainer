"""Does the source audio land on its pictures? Coins and pauses as witnesses (M4 E4-audio).

    audio_sync_check.py <tap session> <audio.cap> --map <MappingUS.map> [--report out.json]
        [--version us] [--before-ms 100] [--after-ms 400]

For each sound the game plays on a frame we can name, it finds the picture
that showed that frame (tap.py's "next" pairing) and the onset of the sound
in the capture audio_capture.py wrote, maps the onset to the tap's QPC ticks
through the fitted audio clock (audio_clock.py) and reports the offset in ms.

The events, read from the per-task regions the tap already copies (both
values sit in the scene .bss span):
- coin: the HUD's coin count rises by one from one frame to the next. The
  count is gHudDisplay.coins (layout `hud_display` + addresses.HUD_COINS_OFF),
  and update_hud_values (level_update.c) plays the coin sound in the very
  frame it steps that count, one coin every other frame. So the sound's frame
  is the task that first shows the new count, which is at most a frame after
  the pickup itself.
- pause: sCurrPlayMode goes from normal (0) to paused (2). The pause screen,
  and with it the pause sound, is first drawn on the next frame
  (play_mode_paused sets the menu mode; the render draws it), so the sound's
  frame is the task after. sCurrPlayMode has no layout row: --map names it,
  and the map must agree with the layout's gHudDisplay and gGlobalTimer.
Only consecutive frames count (gGlobalTimer + 1, from the tap's identity
copy), so a savestate load restoring coins never poses as a pickup; only
isolated events count (no other coin or play-mode change within the search
window), so a red or blue coin's run of coin sounds never shares a window.

Onsets: each event's window runs from --before-ms before its picture's sample
to --after-ms after. Within it the strongest rise of high-frequency energy
gives a first onset; the cleanest of the strongest few, judged by how well it
matches the rest, becomes the kind's template (5 ms before its onset to 40 ms
after), and every event's onset is where that template correlates best,
normalised and on first differences, so the music's bass cannot line up
instead of the sound. The game plays the same
sample each time, so relative onsets are exact to a sample or so; the
template's own onset fixes the absolute value to about a millisecond. An
event whose best correlation stays under 0.5 is reported and left out.

Pass (e4-audio-design.md): for each kind with at least 3 events, the offsets
spread by at most one 30 fps frame, and the latest third agrees with the
earliest within 5 ms. The game's audio runs in its own ~16 ms updates, so
some spread is expected; drift would show as the thirds parting, which the
report also gives without the fitted rate (the nominal 48 kHz from where the
clock's first segment starts), so the fit's contribution is visible.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio_clock as ac  # noqa: E402
import audio_session as asn  # noqa: E402
import tap  # noqa: E402
from sm64_events.memory import addresses as A  # noqa: E402
from sm64_events.memory.layout import layout_for  # noqa: E402

PLAY_MODE_NORMAL, PLAY_MODE_PAUSED = 0, 2          # level_update.h
SOUND_LAG = {"coin": 0, "pause": 1}                # tasks from the RAM change to the frame that plays the sound
ONE_FRAME_MS = 1000 / 30
AGREE_MS = 5.0
MIN_NCC = 0.5
MIN_EVENTS = 3
PRE_MS, TEMPLATE_MS, CANDIDATES = 5, 40, 5
MAP_ROW = re.compile(r"^\s*0x([0-9a-f]{16})\s+(\S+)\s*$")


@dataclass(frozen=True)
class TaskState:
    sequence: int
    timer: int | None
    coins: int | None
    mode: int | None
    present: int | None          # index of the picture that showed this task, or None


@dataclass(frozen=True)
class Event:
    kind: str
    frame: int                   # gGlobalTimer of the frame whose code plays the sound
    task: int                    # that task's sequence
    present: int                 # index of the picture that showed it


def addresses(map_path: Path, version: str) -> dict:
    """{name: address} the check reads, and where each came from."""
    wanted = {"sCurrPlayMode", "gHudDisplay", "gGlobalTimer"}
    symbols = {}
    for line in map_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if (match := MAP_ROW.match(line)) and match[2] in wanted:
            symbols.setdefault(match[2], int(match[1], 16) & 0xFFFFFFFF)
    layout = layout_for(version)
    for symbol, field in (("gHudDisplay", "hud_display"), ("gGlobalTimer", "global_timer")):
        if symbols.get(symbol) != layout.value(field):
            raise SystemExit(f"{map_path}: {symbol} disagrees with layout.{field}; not the {version} map")
    if "sCurrPlayMode" not in symbols:
        raise SystemExit(f"{map_path}: names no sCurrPlayMode")
    return {"coins": layout.hud_display + A.HUD_COINS_OFF, "play_mode": symbols["sCurrPlayMode"]}


def _s16(value: int | None) -> int | None:
    return None if value is None else value - 0x10000 if value & 0x8000 else value


def _region_value(regions: tap.Regions, address: int, size: int) -> int | None:
    phys, at = address & 0x1FFFFFFF, 0
    for begin, count in regions.spans:
        if begin <= phys and phys + size <= begin + count:
            return tap.word_value([(begin, memoryview(regions.data)[at:at + count])], phys, size)
        at += count
    return None


def read_session(session: Path, coins_at: int, mode_at: int) -> tuple[list, list[TaskState]]:
    """(presents, every task's state), one pass over tap.bin and no task kept whole."""
    identity = {name: (address, size) for name, address, size in tap.identity_spec(session)}
    if "global_timer" not in identity:
        raise SystemExit(f"{session}: its arming copied no gGlobalTimer identity")
    timer_at, timer_size = identity["global_timer"]
    presents, index, values = [], {}, {}
    last = None

    def records():
        nonlocal last
        for record in tap.read_records(session / "tap.bin"):
            if isinstance(record, tap.Present):
                index[record.sequence] = len(presents)
                presents.append(ac.Present(record.sequence, record.qpc))
            elif isinstance(record, tap.Task):
                last = record
            elif (isinstance(record, tap.Regions) and record.kind == "task" and last is not None
                  and record.fields["task_index"] == last.fields["task_index"]):
                values[last.sequence] = (_s16(_region_value(record, coins_at, 2)),
                                         _s16(_region_value(record, mode_at, 2)))
            yield record

    states = []
    for task, present in tap.paired(records()):
        coins, mode = values.pop(task.sequence, (None, None))
        states.append(TaskState(task.sequence, tap.word_value(task.extras, timer_at, timer_size), coins, mode,
                                None if present is None else index[present.sequence]))
    return presents, states


def find_events(states: list[TaskState], isolation_frames: int) -> tuple[list[Event], dict]:
    """Isolated coin and pause sounds with the picture of the frame that plays them,
    and how many candidates were left out and why."""
    candidates, changes = [], []
    for i in range(1, len(states)):
        prev, cur = states[i - 1], states[i]
        if prev.timer is None or cur.timer is None:
            continue
        consecutive = cur.timer == (prev.timer + 1) & 0xFFFFFFFF
        if None not in (prev.coins, cur.coins) and cur.coins != prev.coins:
            changes.append(cur.timer)
            if consecutive and cur.coins == prev.coins + 1:
                candidates.append(("coin", i))
        if None not in (prev.mode, cur.mode) and cur.mode != prev.mode:
            changes.append(cur.timer)
            if consecutive and (prev.mode, cur.mode) == (PLAY_MODE_NORMAL, PLAY_MODE_PAUSED):
                candidates.append(("pause", i))
    changes.sort()
    events, skipped = [], Counter()
    for kind, i in candidates:
        j, own = i + SOUND_LAG[kind], states[i].timer
        if j >= len(states) or states[j].timer != (own + SOUND_LAG[kind]) & 0xFFFFFFFF:
            skipped[f"{kind}: the frame that plays it was not recorded"] += 1
            continue
        if states[j].present is None:
            skipped[f"{kind}: that frame was never shown"] += 1
            continue
        near = changes[bisect.bisect_left(changes, own - isolation_frames):
                       bisect.bisect_right(changes, own + isolation_frames)]
        if len(near) > 1:
            skipped[f"{kind}: another coin or pause within the window"] += 1
            continue
        events.append(Event(kind, states[j].timer, states[j].sequence, states[j].present))
    return events, dict(skipped)


def energy_onset(x: np.ndarray, rate: int) -> tuple[int, float]:
    """(sample, strength) of the strongest rise of high-frequency energy: the
    next 8 ms against the 20 ms before, at 1 ms steps, refined to the first
    sample past a tenth of the way up."""
    hop, lead, follow = rate // 1000, 20, 8
    hf = np.diff(x, prepend=x[:1])
    energy = hf * hf
    frames = len(energy) // hop
    if frames <= lead + follow:
        return 0, 0.0
    envelope = energy[:frames * hop].reshape(frames, hop).mean(1)
    total = np.concatenate(([0.0], np.cumsum(envelope)))
    ks = np.arange(lead, frames - follow)
    before = (total[ks] - total[ks - lead]) / lead
    after = (total[ks + follow] - total[ks]) / follow
    ratio = after / (before + max(envelope.mean() * 1e-3, 1e-12))
    best = int(np.argmax(ratio))
    k, level = int(ks[best]), before[best] + 0.1 * (after[best] - before[best])
    start = max(0, (k - 2) * hop)
    above = np.flatnonzero(energy[start:(k + follow) * hop] > level)
    return start + (int(above[0]) if len(above) else 2 * hop), float(ratio[best])


def ncc_scan(x: np.ndarray, template: np.ndarray) -> tuple[int, float]:
    """(lag, value) where `template` correlates best with `x` (normalised, means removed)."""
    m, n = len(template), len(x)
    t = template - template.mean()
    norm = math.sqrt(float((t * t).sum()))
    if n < m or norm == 0:
        return 0, 0.0
    size = 1 << (n + m).bit_length()
    corr = np.fft.irfft(np.fft.rfft(x, size) * np.conj(np.fft.rfft(t, size)), size)[:n - m + 1]
    sums = np.concatenate(([0.0], np.cumsum(x)))
    squares = np.concatenate(([0.0], np.cumsum(x * x)))
    s1, s2 = sums[m:] - sums[:-m], squares[m:] - squares[:-m]
    ncc = corr / (np.sqrt(np.maximum(s2 - s1 * s1 / m, 1e-12)) * norm)
    lag = int(np.argmax(ncc))
    return lag, float(ncc[lag])


def locate_onsets(windows: list[np.ndarray], rate: int) -> tuple[list[tuple[int, float]], int | None]:
    """[(onset sample in its window, correlation)] for one kind's windows, and the
    reference window whose sound became the template (None without one)."""
    pre, length = PRE_MS * rate // 1000, (PRE_MS + TEMPLATE_MS) * rate // 1000
    first = [energy_onset(w, rate) for w in windows]
    # Correlate the first differences: a game sound's energy sits in the kHz, while
    # music's bass, which repeats from window to window, would otherwise line up instead.
    bright = [np.diff(w, prepend=w[:1]) for w in windows]
    strongest = sorted(range(len(windows)), key=lambda i: -first[i][1])[:CANDIDATES]
    best = None
    for c in strongest:
        start = first[c][0] - pre
        if start < 0 or start + length > len(windows[c]):
            continue
        peaks = [ncc_scan(w, bright[c][start:start + length]) for w in bright]
        score = float(np.median([value for _, value in peaks]))
        if best is None or score > best[0]:
            best = (score, c, peaks)
    if best is None:
        return [(0, 0.0)] * len(windows), None
    return [(lag + pre, value) for lag, value in best[2]], best[1]


def _tick_of(clock: ac.AudioClock, sample: int) -> int | None:
    firsts = [s.stream_first for s in clock.segments]
    i = bisect.bisect_right(firsts, sample) - 1
    if i < 0 or sample >= clock.segments[i].stream_first + clock.segments[i].samples:
        return None
    return clock.segments[i].time_of(sample - clock.segments[i].stream_first)


def measure(clock: ac.AudioClock, pcm: np.ndarray, events: list[Event], *,
            before_ms: float = 100.0, after_ms: float = 400.0) -> list[dict]:
    """One row per event: its picture, the sound's onset and their offset in ms."""
    rate, frequency = clock.rate, clock.frequency
    before, after = round(before_ms * rate / 1000), round(after_ms * rate / 1000)
    rows, by_kind = [], {}
    for event in events:
        picture = clock.pictures[event.present]
        row = {"kind": event.kind, "frame": event.frame, "task": event.task, "present": picture.sequence,
               "picture_s": round(picture.ticks / frequency, 4)}
        rows.append(row)
        lo, hi = picture.sample - before, picture.sample + after
        if picture.status != ac.COVERED:
            row["skipped"] = f"picture has no audio ({ac.STATUS_NAMES[picture.status]})"
        elif lo < 0 or hi > len(pcm):
            row["skipped"] = "the window leaves the recording"
        elif any(first < hi and lo < first + count for first, count in clock.losses):
            row["skipped"] = "the window holds lost audio"
        else:
            window = pcm[lo:hi].astype(np.float64).mean(axis=1)
            by_kind.setdefault(event.kind, []).append((row, lo, picture.ticks, window))
    anchor = clock.segments[0] if clock.segments else None
    for entries in by_kind.values():
        onsets, reference = locate_onsets([window for *_, window in entries], rate)
        for i, ((row, lo, ticks, _), (onset, value)) in enumerate(zip(entries, onsets, strict=True)):
            tick = _tick_of(clock, lo + onset)
            row.update(ncc=round(value, 3), reference=i == reference, onset_sample=lo + onset)
            if tick is None or value < MIN_NCC:
                row["skipped"] = "no onset matched the template" if value < MIN_NCC else "onset outside the clock"
                continue
            row["offset_ms"] = round((tick - ticks) * 1000 / frequency, 3)
            nominal = anchor.anchor + (lo + onset - anchor.stream_first) * frequency / rate
            row["nominal_offset_ms"] = round((nominal - ticks) * 1000 / frequency, 3)
    return rows


def summarize(rows: list[dict]) -> dict:
    """Per kind and overall: pass when every kind with enough events spreads by at
    most a frame and its last third agrees with its first within AGREE_MS."""
    kinds = {}
    for kind in sorted({row["kind"] for row in rows}):
        used = sorted((r for r in rows if r["kind"] == kind and "offset_ms" in r), key=lambda r: r["picture_s"])
        entry = {"events": sum(r["kind"] == kind for r in rows), "used": len(used)}
        kinds[kind] = entry
        if len(used) < MIN_EVENTS:
            entry["verdict"] = "insufficient"
            continue
        offsets = np.array([r["offset_ms"] for r in used])
        nominal = np.array([r["nominal_offset_ms"] for r in used])
        minutes = np.array([r["picture_s"] for r in used]) / 60
        third = max(1, len(used) // 3)
        early_late = float(offsets[-third:].mean() - offsets[:third].mean())
        spread = float(offsets.max() - offsets.min())
        entry.update(
            median_ms=round(float(np.median(offsets)), 3), mean_ms=round(float(offsets.mean()), 3),
            spread_ms=round(spread, 3), early_late_ms=round(early_late, 3),
            nominal_early_late_ms=round(float(nominal[-third:].mean() - nominal[:third].mean()), 3),
            slope_ms_per_min=round(float(np.polyfit(minutes, offsets, 1)[0]), 3) if np.ptp(minutes) else None,
            minutes=[round(float(minutes[0]), 2), round(float(minutes[-1]), 2)],
            verdict="pass" if spread <= ONE_FRAME_MS and abs(early_late) <= AGREE_MS else "fail")
    judged = [entry["verdict"] for entry in kinds.values() if entry["verdict"] != "insufficient"]
    verdict = "insufficient" if not judged else "pass" if all(v == "pass" for v in judged) else "fail"
    medians = {kind: entry["median_ms"] for kind, entry in kinds.items() if "median_ms" in entry}
    return {"verdict": verdict, "kinds": kinds,
            "kind_median_gap_ms": round(max(medians.values()) - min(medians.values()), 3)
            if len(medians) > 1 else None,
            "rule": f"per kind with >= {MIN_EVENTS} events: spread <= {ONE_FRAME_MS:.1f} ms and "
                    f"|last third - first third| <= {AGREE_MS} ms"}


def run(session: Path, capture: Path, map_path: Path, *, version: str = "us",
        before_ms: float = 100.0, after_ms: float = 400.0) -> dict:
    where = addresses(map_path, version)
    presents, states = read_session(session, where["coins"], where["play_mode"])
    if not any(state.coins is not None for state in states):
        raise SystemExit(f"{session}: no task region holds the HUD coin count (armed with --no-scene-state?)")
    isolation = math.ceil((before_ms + after_ms) / ONE_FRAME_MS)
    events, skipped = find_events(states, isolation)
    clock, pcm = asn.build(session, capture, presents=presents)
    rows = measure(clock, pcm, events, before_ms=before_ms, after_ms=after_ms)
    stats = {key: clock.stats[key] for key in ("drift_ppm", "jitter_us", "holes", "overruns", "lost_samples",
                                                "pictures", "epochs")}
    return {**summarize(rows), "clock": stats, "tasks": len(states), "presents": len(presents),
            "left_out": skipped, "addresses": {key: f"{value:#010x}" for key, value in where.items()},
            "events": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("session")
    parser.add_argument("capture")
    parser.add_argument("--map", required=True, help="STROOP MappingUS.map (names sCurrPlayMode)")
    parser.add_argument("--version", choices=("us", "jp"), default="us")
    parser.add_argument("--before-ms", type=float, default=100.0)
    parser.add_argument("--after-ms", type=float, default=400.0)
    parser.add_argument("--report", help="write the full report, every event included, here")
    args = parser.parse_args()
    report = run(Path(args.session), Path(args.capture), Path(args.map), version=args.version,
                 before_ms=args.before_ms, after_ms=args.after_ms)
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "events"}, indent=2))
    return {"pass": 0, "fail": 1}.get(report["verdict"], 3)


if __name__ == "__main__":
    raise SystemExit(main())
