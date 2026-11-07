"""G0-G3 on a recorded session: is it the game's own sound, and do the stems split it? (M4 stems)

    stems_probe.py from-tap <tap or live spool session> --rom <z64> --map <MappingUS.map> [--lle-src <cxd4>]
        [--capture <audio.cap>] [--report out.json] [--wavs <dir>] [--segment N] [--vis N]

A session armed with the audio spans (tap.arming_text: vi_region= and vi_dmem=1) holds, at every
VI, the audio state the wrapper's UpdateScreen saw: a one-shot tap in its tap.bin, the live spool
(its folder; live_spool.Lease asks for the spans) in its segments, read from --segment's first
byte (its writer keys rebuild the state) for at most --vis VIs. Four gates, each with a verdict:

G0 capture point. At the VI the audio task the game dispatched waits in gSPTaskMesgQueue (it is
   &gAudioTasks[gAudioTaskIndex] and its list is the command buffer that index names), no audio
   task is running (sCurrentAudioSPTask is null), and the audio frame count moved by one since
   the last VI. Then the VI's bytes are that task's whole input. A VI with no task waiting
   holds only when the game made none since the last VI (judge()); a task made and gone means
   the VI came too late, and fails.
G1 renderer. The ROM's own microcode (cxd4 through stems_lle.c) runs each task from its VI's
   bytes. The samples, note state and reverb ring it writes, and the list buffers it leaves in
   DMEM, must equal what the next VI captured, bit for bit, on every task: the stems below then
   come from the program that made his sound. Without --lle-src G1 and G3 do not run.
G2 as heard. The game's output (the AI buffers the RSP wrote, in the order the AI plays them)
   against the loopback recording (audio_capture.py): resampled to the capture's rate, aligned
   per window to a fraction of a sample, with the rate fitted across windows. It reports the SNR
   per window and the windows that leave the fit (Project64's audio plugin dropping or repeating
   samples). It describes what "as heard" adds; it does not gate the stems.
G3 partition. Each task renders three ways from the same bytes (the mix; music only; effects
   only, the other side's mixing sent to scratch DMEM by stems_probe.edited_list): every
   mix-writing command has a known owner and every envelope mixer a note, and music + effects
   stays within STEM_SUM_LSB of the mix once the stems' own reverb rings have run STEM_SETTLE_S.

Exit 0 when every gate that ran passed, 1 when one failed, 2 for unusable input, 3 when a gate
had nothing to judge. Sessions, captures and WAVs are private: keep them out of the repository.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio_clock as ac  # noqa: E402
import audio_session as asn  # noqa: E402
import stems_probe as sp  # noqa: E402
import tap  # noqa: E402
from sm64_events.memory.layout import layout_for  # noqa: E402

STEM_SUM_LSB = 8
STEM_SETTLE_S = 1.0
M_AUDTASK, TASK_BYTES, TASK_WORDS = 2, 0x50, 16
NO_TASK = "no audio task waits"
DMEM_LISTS = (0x5C0, 0xF80)                # the audio microcode's buffers (ABI1 DMEM_BASE ..)
REVERB = {"useReverb": 1, "bufSizePerChannel": 0x10, "left": 0x14, "right": 0x18}   # struct SynthesisReverb
# The map symbol each layout span starts at: the checker reads struct fields by symbol.
LAYOUT_SYMBOLS = {"audio_heap_start": "gAudioHeap", "audio_globals_start": "gAudioGlobalsStartMarker",
                  "sp_task_queue_start": "gSPTaskMesgQueue", "sp_task_pointers_start": "gActiveSPTask"}
G2_WINDOW_S, G2_STEP_S, G2_SEARCH_S, G2_ENVELOPE_HZ = 1.0, 2.0, 0.08, 1000
G2_FIT_MS, G2_PASS_DB, G2_CLEAN_FRACTION = 1.0, 40.0, 0.8
LANCZOS = 8


class WordRam:
    """N64 reads over RDRAM in Project64's word order (what Owners and the gates read through)."""

    def __init__(self, ram: np.ndarray, sym: dict[str, int]):
        self.ram, self.sym = ram, sym

    def u32(self, address: int) -> int:
        at = sp.phys(address)
        return int.from_bytes(self.ram[at:at + 4].tobytes(), "little")

    def s16(self, address: int) -> int:
        at = sp.phys(address) ^ 2
        value = int.from_bytes(self.ram[at:at + 2].tobytes(), "little")
        return value - 0x10000 if value & 0x8000 else value

    def u8(self, address: int) -> int:
        return int(self.ram[sp.phys(address) ^ 3])


def list_commands(ram: np.ndarray, alist: int, size: int) -> list[tuple[int, int]]:
    words = np.frombuffer(ram[sp.phys(alist):sp.phys(alist) + size].tobytes(), "<u4")
    return list(zip(words[0::2].tolist(), words[1::2].tolist(), strict=True))


@dataclass
class Pending:
    """The audio task waiting at one VI, as the game's own globals name it."""
    vi: int
    ok: bool
    reason: str
    frame: int              # gAudioFrameCount: create_next_audio_frame_task counts every call
    made: tuple             # (gAudioTaskIndex, gCurrAiBufferIndex): both move only when it makes a task
    words: tuple = ()
    ai_buffer: int = 0
    ai_length: int = 0


def pending_task(ram: WordRam, sym: dict[str, int], vi: int) -> Pending:
    """G0 at one VI: the audio task the game dispatched, waiting and not yet run."""
    queue, tasks = sym["gSPTaskMesgQueue"], sym["gAudioTasks"]
    valid, first, slots, buffer = (ram.u32(queue + offset) for offset in (8, 12, 16, 20))
    frame, index, ai = (ram.u32(sym[name]) for name in ("gAudioFrameCount", "gAudioTaskIndex", "gCurrAiBufferIndex"))
    made = (index, ai)
    if not slots or valid > slots or index > 1:
        return Pending(vi, False, "the task queue does not read as one", frame, made)
    expected = tasks + TASK_BYTES * index
    messages = [ram.u32(buffer + 4 * ((first + i) % slots)) for i in range(valid)]
    audio = [message for message in messages if message in (tasks, tasks + TASK_BYTES)]
    if audio != [expected]:
        return Pending(vi, False, NO_TASK if not audio else f"{len(audio)} audio tasks wait, not this frame's",
                       frame, made)
    if ram.u32(sym["sCurrentAudioSPTask"]):
        return Pending(vi, False, "an audio task is running", frame, made)
    words = tuple(ram.u32(expected + 4 * i) for i in range(TASK_WORDS))
    if words[0] != M_AUDTASK or words[12] != ram.u32(sym["gAudioCmdBuffers"] + 4 * index):
        return Pending(vi, False, "the waiting task is not this frame's command list", frame, made)
    return Pending(vi, True, "", frame, made, words, ram.u32(sym["gAiBuffers"] + 4 * ai),
                   ram.s16(sym["gAiBufferLengths"] + 2 * ai))


def judge(pending: Pending, last: Pending | None) -> str | None:
    """G0's reading of one VI against the one before: None when it holds, else why not. With
    no task waiting, the VI holds when the game made none since the last (its task and buffer
    indexes did not move: create_next_audio_frame_task returns before them during an audio
    load, and an idle audio thread does not run it at all)."""
    if last is None:
        return None if pending.ok or pending.reason == NO_TASK else pending.reason
    step = pending.frame - last.frame
    if pending.ok:
        return None if step == 1 else f"the audio frame count moved by {step} with a task waiting"
    if pending.reason == NO_TASK and pending.made == last.made:
        return None
    return pending.reason if pending.reason != NO_TASK else "a task was made since the last VI and none waits"


def reverb_ring(ram: WordRam, sym: dict[str, int]) -> tuple[int, int] | None:
    base = sym["gSynthesisReverb"]
    if not ram.u8(base + REVERB["useReverb"]):
        return None
    size = ram.u32(base + REVERB["bufSizePerChannel"])
    return sp.phys(ram.u32(base + REVERB["left"])), sp.phys(ram.u32(base + REVERB["right"])) + size * 2


def rsp_regions(ram: WordRam, sym: dict[str, int], pending: Pending) -> list[tuple[str, int, int]]:
    """What the task writes in RDRAM: its samples, each note's RSP state, the reverb ring."""
    regions = [("ai buffer", sp.phys(pending.ai_buffer), pending.ai_length * 4)]
    notes, count = ram.u32(sym["gNotes"]), ram.u8(sym["gMaxSimultaneousNotes"] + 3)
    for index in range(count):
        buffers = ram.u32(notes + index * sp.NOTE_BYTES + sp.NOTE["synthesisBuffers"])
        if buffers:
            regions.append((f"note {index} state", sp.phys(buffers), sp.SYNTH["samples"]))
    ring = reverb_ring(ram, sym)
    if ring:
        regions.append(("reverb ring", ring[0], ring[1] - ring[0]))
    return regions


@dataclass
class Prediction:
    pending: Pending
    regions: list           # (name, begin, bytes) the task writes
    expected: list | None   # their bytes as the microcode wrote them (None without --lle-src)
    dmem: np.ndarray | None


STEMS = (("music", sp.MUSIC_PLAYERS), ("effects", sp.EFFECT_PLAYERS))


def vi_spans(session: Path) -> list[tuple[int, int]]:
    """The RDRAM spans a session copied at every VI (its session.txt vi_region lines)."""
    return [tuple(int(part, 16) if i == 0 else int(part) for i, part in enumerate(line.partition("=")[2].split(":")))
            for line in (session / "session.txt").read_text(encoding="ascii").splitlines()
            if line.startswith("vi_region=")]


class Splitter:
    """The ROM's own audio microcode (cxd4) over the VI states a recording holds: a waiting task as
    the game dispatched it (what G1 compares with what Project64's RSP wrote), and its two stems.
    A stem renders the same task from the same bytes with the other side's notes mixing into
    scratch DMEM (stems_probe.edited_list), and carries its own reverb ring from task to task: a
    new ring (an audio reset) starts both silent, so the stems sum to the mix STEM_SETTLE_S after
    a reset or after the first task split. The checker's G1 and G3 and the scenes' stems
    (stems_live.py) render through this one class."""

    def __init__(self, lle: sp.Lle, rom: bytes, symbol_map: Path, spans: list[tuple[int, int]], sym: dict):
        self.lle, self.spans, self.sym = lle, spans, sym
        self.work = np.zeros(sp.RDRAM_BYTES, np.uint8)
        ram, size, rom_at = sp.main_section(symbol_map)       # the boot and audio microcode, from the ROM
        image = np.frombuffer(rom, np.uint8, size, rom_at).copy()
        self.work[sp.phys(ram):sp.phys(ram) + size] = sp.swap32(image)
        self.owners: sp.Owners | None = None
        self.rings: dict[str, np.ndarray] = {}
        self.ring_key = None
        self.since_reset = 0.0

    def load(self, state: tap.ViState) -> None:
        for begin, size in self.spans:
            self.work[begin:begin + size] = state.ram[begin:begin + size]
        self.lle.sp[:0x1000] = state.dmem

    def mix(self, pending: Pending, state: tap.ViState) -> int:
        """Runs the task as the game dispatched it: its output is then in self.work, its list
        buffers in self.lle.dmem. The microcode's status: 0 when it reached its BREAK."""
        self.load(state)
        return self.lle.run(self.work, pending.words)

    def restart(self) -> None:
        """The next task follows a gap (lost VIs, another session): both stems start silent."""
        self.ring_key, self.owners = (), None     # () is no ring: the next split starts them anew

    def split(self, pending: Pending, state: tap.ViState, ram: WordRam) -> tuple[dict, dict]:
        """The two stems of the task waiting at this VI ({"music", "effects"}: int16 stereo of the
        task's own length, None for a stem whose list could not be edited) and what the partition
        saw: unknown owners, unattributed envelope mixers, voices handed over, edit errors,
        whether the ring started anew, and whether the stems had settled."""
        if self.owners is None:
            self.owners = sp.Owners(ram)
        alist, size = pending.words[12], pending.words[13]
        cmds = list_commands(state.ram, alist, size)
        notes = self.owners.update()
        ring = reverb_ring(ram, self.sym)
        owners, stats = sp.attribute(cmds, notes, ring or (0, 0))
        seen = {"unknown_owner_commands": owners.count("unknown"), "unattributed_envmixers": stats["unattributed_mix"],
                "handovers": len(stats["handovers"]), "edit_errors": 0, "ring_reset": ring != self.ring_key}
        if seen["ring_reset"]:               # an audio reset allocated a new ring: the stems start silent
            self.ring_key = ring
            self.rings = {stem: np.zeros(0 if ring is None else ring[1] - ring[0], np.uint8) for stem, _ in STEMS}
            self.since_reset = 0.0
        seen["settled"] = self.since_reset >= STEM_SETTLE_S
        renders = {}
        for stem, keep in STEMS:
            try:
                edited = sp.edited_list(cmds, sp.muted_mask(owners, keep))
            except ValueError:
                seen["edit_errors"] += 1
                renders[stem] = None
                continue
            self.load(state)
            if ring:
                self.work[ring[0]:ring[1]] = self.rings[stem]
            sp.put_list(self.work, alist, edited)
            self.lle.run(self.work, pending.words)
            if ring:
                self.rings[stem] = self.work[ring[0]:ring[1]].copy()
            renders[stem] = sp.ai_samples(self.work, pending.ai_buffer, pending.ai_length)
        self.since_reset += pending.ai_length / sp.AI_RATE
        return renders, seen


class Checker:
    def __init__(self, args):
        self.args = args
        self.rom = args.rom.read_bytes()
        self.sym = sp.load_symbols(args.map)
        layout = layout_for("us")
        wrong = [field for field, symbol in LAYOUT_SYMBOLS.items() if layout.value(field) != self.sym[symbol]]
        if wrong:
            raise SystemExit(f"{args.map}: the map disagrees with layout.py on {wrong}")
        header = tap.session_header(args.session)
        self.rdram_size = int(header["rdram_size"])
        self.spans = vi_spans(args.session)
        self.splitter = None
        if args.lle_src:
            lle = sp.Lle(sp.build_lle(args.lle_src, args.build), bytes(0x1000), bytes(0x1000))
            self.splitter = Splitter(lle, self.rom, args.map, self.spans, self.sym)
        self.prediction: Prediction | None = None
        self.g0 = {"vis": 0, "pending": 0, "no_task_made": 0, "unexplained": [], "lost_vis": 0}
        self.g1 = {"compared": 0, "identical": 0, "mismatches": [], "skipped_lost": 0, "statuses": {}}
        self.g3 = {"tasks": 0, "unknown_owner_commands": 0, "unattributed_envmixers": 0, "handovers": 0,
                   "edit_errors": 0, "ring_resets": 0}
        self.real: list[tuple[int, np.ndarray]] = []         # (vi, the samples the RSP wrote)
        self.renders = {name: [] for name in ("mix", "music", "effects")}
        self.settled: list[bool] = []
        self.last: Pending | None = None
        self.last_vi: int | None = None

    def visit(self, record: tap.ViRegions, state: tap.ViState) -> None:
        vi = record.fields["vi_index"]
        ram = WordRam(state.ram, self.sym)
        if self.last_vi is not None and vi != self.last_vi + 1:
            self.g0["lost_vis"] += vi - self.last_vi - 1
            if self.splitter is not None:
                self.splitter.restart()       # the stems missed the lost tasks: they start silent
        if self.prediction is not None:
            self.settle(self.prediction, state, vi)
        pending = pending_task(ram, self.sym, vi)
        g0 = self.g0
        g0["vis"] += 1
        g0["pending"] += pending.ok
        reason = judge(pending, self.last if self.last_vi == vi - 1 else None)
        if reason is not None:
            g0["unexplained"].append({"vi": vi, "reason": reason})
        elif not pending.ok:
            g0["no_task_made"] += 1
        self.last, self.last_vi = pending, vi
        self.prediction = self.render(pending, state, ram) if pending.ok else None

    def render(self, pending: Pending, state: tap.ViState, ram: WordRam) -> Prediction:
        regions = rsp_regions(ram, self.sym, pending)
        splitter = self.splitter
        if splitter is None:
            return Prediction(pending, regions, None, None)
        status = splitter.mix(pending, state)
        self.g1["statuses"][str(status)] = self.g1["statuses"].get(str(status), 0) + 1
        expected = [splitter.work[begin:begin + size].copy() for _, begin, size in regions]
        dmem = splitter.lle.dmem[DMEM_LISTS[0]:DMEM_LISTS[1]].copy()
        self.stems(pending, state, ram, expected[0])
        return Prediction(pending, regions, expected, dmem)

    def stems(self, pending: Pending, state: tap.ViState, ram: WordRam, mix_bytes: np.ndarray) -> None:
        """G3 for one task: the mix the microcode just wrote, then each stem from the same bytes."""
        mix = sp.swap32(mix_bytes.copy()).view(">i2").astype(np.int16).reshape(-1, 2)
        renders, seen = self.splitter.split(pending, state, ram)
        g3 = self.g3
        g3["tasks"] += 1
        for name in ("unknown_owner_commands", "unattributed_envmixers", "handovers", "edit_errors"):
            g3[name] += seen[name]
        g3["ring_resets"] += seen["ring_reset"]
        for name, samples in {"mix": mix, **renders}.items():
            self.renders[name].append(np.zeros_like(mix) if samples is None else samples)
        self.settled.append(seen["settled"])

    def settle(self, prediction: Prediction, state: tap.ViState, vi: int) -> None:
        """G1 and G2's stream for the task of the last VI, from what this VI captured."""
        pending = prediction.pending
        if vi != pending.vi + 1:
            self.g1["skipped_lost"] += 1
            return
        _, begin, size = prediction.regions[0]
        self.real.append((pending.vi, sp.swap32(state.ram[begin:begin + size].copy()).view(">i2")
                          .astype(np.int16).reshape(-1, 2)))
        if prediction.expected is None:
            return
        self.g1["compared"] += 1
        differ = [region for region, bytes_ in zip(prediction.regions, prediction.expected, strict=True)
                  if not np.array_equal(state.ram[region[1]:region[1] + region[2]], bytes_)]
        if not np.array_equal(state.dmem[DMEM_LISTS[0]:DMEM_LISTS[1]], prediction.dmem):
            differ.append(("DMEM list buffers", DMEM_LISTS[0], DMEM_LISTS[1] - DMEM_LISTS[0]))
        if not differ:
            self.g1["identical"] += 1
            return
        if len(self.g1["mismatches"]) < 20:
            got = self.real[-1][1].astype(np.int32)
            want = sp.swap32(prediction.expected[0].copy()).view(">i2").astype(np.int32).reshape(-1, 2)
            self.g1["mismatches"].append({"vi": pending.vi, "regions": [region[0] for region in differ][:6],
                                          "ai_max_abs": int(np.abs(got - want).max()) if len(got) else 0})

    def verdicts(self) -> dict:
        g0, g1, g3 = self.g0, self.g1, self.g3
        out = {"G0": dict(g0, unexplained=g0["unexplained"][:20], unexplained_count=len(g0["unexplained"]),
                          verdict="nothing to judge" if not g0["pending"] else
                          "pass" if not g0["unexplained"] else "fail")}
        if self.splitter is None:
            out["G1"] = out["G3"] = {"verdict": "not run", "reason": "no --lle-src"}
            return out
        clean_runs = set(g1["statuses"]) <= {"0"}         # every task reached the microcode's BREAK
        out["G1"] = dict(g1, verdict="nothing to judge" if not g1["compared"] else
                         "pass" if g1["identical"] == g1["compared"] and clean_runs else "fail")
        out["G3"] = dict(g3, **self.partition())
        return out

    def partition(self) -> dict:
        if not self.renders["mix"]:
            return {"verdict": "nothing to judge"}
        worst, unsettled = 0, 0
        for mix, music, effects, settled in zip(self.renders["mix"], self.renders["music"], self.renders["effects"],
                                                self.settled, strict=True):
            gap = int(np.abs(music.astype(np.int32) + effects - mix.astype(np.int32)).max()) if len(mix) else 0
            if settled:
                worst = max(worst, gap)
            else:
                unsettled = max(unsettled, gap)
        g3 = self.g3
        ok = (not g3["unknown_owner_commands"] and not g3["unattributed_envmixers"] and not g3["edit_errors"]
              and worst <= STEM_SUM_LSB)
        return {"stem_sum_max_abs_settled": worst, "stem_sum_max_abs_before_settling": unsettled,
                "verdict": "pass" if ok else "fail"}

    def game_stream(self) -> tuple[np.ndarray, list[tuple[int, int]]]:
        """The RSP's samples in play order, and the (sample, count) holes a lost VI left as silence."""
        parts, holes, at, previous = [], [], 0, None
        for vi, samples in self.real:
            if previous is not None and vi != previous + 1 and parts:
                hole = len(parts[-1]) * (vi - previous - 1)
                parts.append(np.zeros((hole, 2), np.int16))
                holes.append((at, hole))
                at += hole
            parts.append(samples)
            at += len(samples)
            previous = vi
        return (np.concatenate(parts) if parts else np.zeros((0, 2), np.int16)), holes


# --- G2: the game's output against the loopback recording ---
LANCZOS_PHASES = 1 << 12        # a position's fraction to 1/4096 sample: the kernel comes from a table
_TABLE: list = []


def _lanczos_table() -> np.ndarray:
    """The Lanczos kernel's taps at every 1/LANCZOS_PHASES fraction (computing sinc per sample
    was most of a scene's stems time)."""
    if not _TABLE:
        fractions = np.arange(LANCZOS_PHASES + 1) / LANCZOS_PHASES
        distance = np.arange(-LANCZOS + 1, LANCZOS + 1)[None, :] - fractions[:, None]
        _TABLE.append((np.sinc(distance) * np.sinc(distance / LANCZOS)).astype(np.float32))
    return _TABLE[0]


def interp(x: np.ndarray, positions: np.ndarray) -> np.ndarray:
    """Lanczos interpolation of `x` at fractional sample `positions` (zero outside): mono, or
    (samples, channels) with every channel read through one kernel (stems_layer's tracks)."""
    taps = np.arange(-LANCZOS + 1, LANCZOS + 1)
    channels = None if x.ndim == 1 else x.shape[1]
    pad = np.zeros((LANCZOS,) if channels is None else (LANCZOS, channels), np.float32)
    padded = np.concatenate([pad, x.astype(np.float32), pad, pad[:1]])
    out = np.empty((len(positions),) if channels is None else (len(positions), channels), np.float32)
    for lo in range(0, len(positions), 1 << 15):
        t = positions[lo:lo + (1 << 15)]
        base = np.floor(t).astype(np.int64)
        kernel = _lanczos_table()[np.rint((t - base) * LANCZOS_PHASES).astype(np.int64)]
        index = np.clip(base[:, None] + taps[None, :] + LANCZOS, 0, len(padded) - 1)
        if channels is None:
            out[lo:lo + len(t)] = (padded[index] * kernel).sum(axis=1)
        else:
            out[lo:lo + len(t)] = np.einsum("mt,mtc->mc", kernel, padded[index])
    return out


def envelope(x: np.ndarray, rate: float) -> np.ndarray:
    step = max(1, int(round(rate / G2_ENVELOPE_HZ)))
    usable = len(x) // step * step
    return np.abs(x[:usable]).reshape(-1, step).mean(axis=1)


def xcorr_lag(a: np.ndarray, b: np.ndarray) -> tuple[int, float]:
    """The shift of `a` inside `b` (b[lag + i] ~ a[i]) with the best normalised correlation."""
    size = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    spectrum = np.fft.irfft(np.fft.rfft(b, size) * np.conj(np.fft.rfft(a, size)), size)[:len(b) - len(a) + 1]
    lag = int(np.argmax(spectrum))
    norm = float(np.linalg.norm(a) * np.linalg.norm(b[lag:lag + len(a)])) or 1.0
    return lag, float(spectrum[lag]) / norm


def window_fit(game: np.ndarray, capture: np.ndarray, start: int, ratio: float, lag: float,
               stretch: float = 1.0, *, seconds: float = G2_WINDOW_S, search_s: float = G2_SEARCH_S) -> dict | None:
    """One window of game samples (32006 Hz, `seconds` long) placed at capture position
    start * ratio + lag (within search_s), the game read `stretch` times as fast as nominal (the
    fitted drift): the integer lag by correlation, then the fraction and gain that leave the
    least residual."""
    count = int(seconds * sp.AI_RATE * ratio)
    search = int(search_s * sp.AI_RATE * ratio)
    centre = int(round(start * ratio + lag))
    lo, hi = centre - search, centre + count + search
    if lo < 0 or hi > len(capture):
        return None
    step = stretch / ratio
    reference = interp(game, start + np.arange(count) * step)
    if not np.any(reference):
        return None
    shift, ncc = xcorr_lag(reference, capture[lo:hi])
    c = capture[lo + shift:lo + shift + count]

    def fit(fraction: float) -> tuple[float, float, float]:
        y = interp(game, start + (np.arange(count) - fraction) * step)
        gain = float(np.dot(c, y) / (np.dot(y, y) or 1.0))
        return float(np.sum((c - gain * y) ** 2)), fraction, gain
    # The residual is near-parabolic in the fraction: three samples place its floor, and three
    # more a tenth of a sample apart place it again (broadband sound bends the wide parabola:
    # the first estimate alone sat 0.05 sample off on synthetic noise, 26 dB of a stem's sum).
    tried = [fit(-0.5), fit(0.0), fit(0.5)]
    curve = tried[0][0] - 2 * tried[1][0] + tried[2][0]
    if curve > 0:
        tried.append(fit(float(np.clip(0.25 * (tried[0][0] - tried[2][0]) / curve, -0.5, 0.5))))
    best = min(tried)
    near = [fit(best[1] - 0.1), best, fit(best[1] + 0.1)]
    curve = near[0][0] - 2 * near[1][0] + near[2][0]
    if curve > 0:
        tried.append(fit(float(np.clip(best[1] + 0.05 * (near[0][0] - near[2][0]) / curve, -0.6, 0.6))))
    residual, fraction, gain = min([*tried, near[0], near[2]])
    power = float(np.sum(c ** 2))
    return {"game_s": round(start / sp.AI_RATE, 3), "lag": lo + shift + fraction - start * ratio, "ncc": round(ncc, 4),
            "gain": round(gain, 4), "snr_db": round(10 * np.log10(power / residual), 2) if residual else 99.0}


def as_heard(game_stereo: np.ndarray, holes: list[tuple[int, int]], capture_path: Path) -> dict:
    rate, _frequency, chunks, payloads = asn.read_capture(capture_path)
    capture = ac.stream_pcm(chunks, payloads).astype(np.float32).mean(axis=1)
    game = game_stereo.astype(np.float32).mean(axis=1)
    if len(game) < 4 * sp.AI_RATE or len(capture) < 4 * rate:
        return {"verdict": "nothing to judge", "reason": "under four seconds of game output or capture"}
    ratio = rate / sp.AI_RATE
    # Coarse: millisecond envelopes of a stretch of the game's middle against the whole capture.
    game_env, capture_env = envelope(game, sp.AI_RATE), envelope(capture, rate)
    stretch = min(len(game_env) // 2, len(capture_env) // 2, 60 * G2_ENVELOPE_HZ)
    middle = len(game_env) // 4
    shift, coarse_ncc = xcorr_lag(game_env[middle:middle + stretch], capture_env)
    lag = (shift - middle) * rate / G2_ENVELOPE_HZ
    starts = [start for start in range(0, len(game) - int((G2_WINDOW_S + 1) * sp.AI_RATE), int(G2_STEP_S * sp.AI_RATE))
              if not any(at < start + G2_WINDOW_S * sp.AI_RATE and start < at + n for at, n in holes)]
    # Pass 1 follows the lag window to window at the nominal rate; its fit gives the drift that
    # pass 2 reads the game at, so a window's residual is the content's, not the clocks'.
    first = []
    for start in starts:
        row = window_fit(game, capture, start, ratio, lag)
        if row is not None:
            first.append(row)
            lag = row["lag"]
    if len(first) < 3:
        return summarize_windows(first, rate, coarse_ncc)
    slope, offset = np.polyfit([w["game_s"] for w in first], [w["lag"] for w in first], 1)
    windows = [row for row in (window_fit(game, capture, start, ratio, offset + slope * start / sp.AI_RATE,
                                          1 - slope / rate) for start in starts) if row is not None]
    return summarize_windows(windows, rate, coarse_ncc)


def summarize_windows(windows: list[dict], rate: int, coarse_ncc: float) -> dict:
    if len(windows) < 3:
        return {"verdict": "nothing to judge", "reason": "fewer than three aligned windows", "windows": windows}
    t = np.array([w["game_s"] for w in windows])
    lags = np.array([w["lag"] for w in windows])
    slope, offset = np.polyfit(t, lags, 1)
    fit_samples = G2_FIT_MS * rate / 1000
    clean = [w for w, lag, at in zip(windows, lags, t, strict=True) if abs(lag - (slope * at + offset)) <= fit_samples]
    snrs = np.array([w["snr_db"] for w in clean]) if clean else np.array([0.0])
    ok = len(clean) >= G2_CLEAN_FRACTION * len(windows) and float(np.median(snrs)) >= G2_PASS_DB
    return {"verdict": "pass" if ok else "fail", "windows": len(windows), "clean_windows": len(clean),
            "rate_ppm": round(slope / rate * 1e6, 1), "offset_ms": round(offset / rate * 1000, 2),
            "snr_db_median": round(float(np.median(snrs)), 2), "snr_db_p10": round(float(np.percentile(snrs, 10)), 2),
            "coarse_envelope_ncc": round(coarse_ncc, 3),
            "off_fit_at_s": [w["game_s"] for w in windows if w not in clean][:30],
            "low_snr_at_s": [w["game_s"] for w in clean if w["snr_db"] < G2_PASS_DB - 10][:30]}


def write_wavs(out: Path, checker: Checker, game: np.ndarray) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    paths = {"game-output": out / "stm-session-game-output.wav"}
    sp.write_wav(paths["game-output"], game)
    for name, parts in checker.renders.items():
        if parts:
            paths[name] = out / f"stm-session-{name}.wav"
            sp.write_wav(paths[name], np.concatenate(parts))
    return {name: str(path) for name, path in paths.items()}


def vi_source(args):
    """The session's VI records: a one-shot tap's tap.bin, or the live spool's segments from
    --segment on (stems_live.session_vi_records); None when it has neither."""
    if (args.session / "tap.bin").is_file():
        return tap.read_records(args.session / "tap.bin")
    if (args.session / "spool-table.bin").is_file():
        import stems_live
        return stems_live.session_vi_records(args.session, first_segment=getattr(args, "segment", None))
    return None


def run(args) -> int:
    source = vi_source(args)
    if source is None:
        print(f"{args.session}: no tap.bin and no live spool segments", file=sys.stderr)
        return 2
    checker = Checker(args)
    limit = getattr(args, "vis", 0) or 0
    for count, (record, state) in enumerate(tap.vi_states(source, checker.rdram_size)):
        if limit and count >= limit:
            break
        checker.visit(record, state)
    report = {"session": str(args.session), "vi_spans": [[hex(b), n] for b, n in checker.spans],
              "gates": checker.verdicts()}
    game, holes = checker.game_stream()
    report["game_output"] = {"seconds": round(len(game) / sp.AI_RATE, 3), "holes": len(holes)}
    report["gates"]["G2"] = as_heard(game, holes, args.capture) if args.capture else {"verdict": "not run",
                                                                                      "reason": "no --capture"}
    if args.wavs:
        report["wavs"] = write_wavs(args.wavs, checker, game)
    text = json.dumps(report, indent=1)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    print(json.dumps({gate: body["verdict"] for gate, body in report["gates"].items()}))
    verdicts = [body["verdict"] for body in report["gates"].values()]
    if "fail" in verdicts:
        return 1
    return 3 if "nothing to judge" in verdicts else 0
