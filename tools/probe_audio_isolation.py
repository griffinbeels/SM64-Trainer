"""Prove the recorder captures ONE process's audio and nothing else.

    uv run python tools/probe_audio_isolation.py

Needs ffmpeg + ffplay on PATH. Plays two quiet tones from two separate
processes at once (440 Hz and 880 Hz), captures only the 440 Hz process
through the real ProcessAudioSource, and reports the power at each.

Why two processes: one sound proves the tap is alive, never that it is
isolated. Device-wide loopback passes a one-tone test perfectly — that is
exactly the bug this guards (a viewer heard calls and music in saved clips,
2026-07-30). Isolation only shows up as the ABSENCE of a second source you
know is playing.

The inverse trap, which cost six weeks: a `winsound.Beep` is emitted by the
kernel's beep path, not by the calling process's audio session, so process
loopback is CORRECT to return silence for it. Process loopback was retired
on 2026-06-11 on exactly that reading. A capture path can only be judged by
a sound whose SOURCE PROCESS you chose.
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sm64_events.replay.audio import ProcessAudioSource  # noqa: E402

RATE = 48000
TARGET_HZ, OTHER_HZ = 440, 880
CAPTURE_S = 4.0
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def make_tone(ffmpeg: str, path: Path, hz: int) -> None:
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i",
         f"sine=frequency={hz}:duration=60:sample_rate={RATE}",
         "-af", "volume=0.06",   # audible on the speakers, deliberately quiet
         "-ac", "2", str(path)],
        capture_output=True, creationflags=NO_WINDOW, check=True)


def play(ffplay: str, path: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
        creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def power_at(pcm: np.ndarray, hz: float) -> float:
    """Amplitude of the `hz` component of an (n, 2) int16 capture."""
    mono = pcm.astype(np.float64).mean(axis=1)
    mono = mono[: (len(mono) // RATE) * RATE]     # whole seconds -> exact bins
    if not len(mono):
        return 0.0
    spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
    freqs = np.fft.rfftfreq(len(mono), 1 / RATE)
    return float(spectrum[(freqs > hz - 15) & (freqs < hz + 15)].max() / len(mono))


def main() -> int:
    ffmpeg, ffplay = shutil.which("ffmpeg"), shutil.which("ffplay")
    if not (ffmpeg and ffplay):
        print("ffmpeg and ffplay must be on PATH")
        return 2

    with tempfile.TemporaryDirectory(prefix="sm64-audio-probe-") as scratch:
        tones = []
        for hz in (TARGET_HZ, OTHER_HZ):
            wav = Path(scratch) / f"{hz}.wav"
            make_tone(ffmpeg, wav, hz)
            tones.append(wav)

        players = [play(ffplay, wav) for wav in tones]
        chunks: list[np.ndarray] = []
        source = ProcessAudioSource(pid=players[0].pid, rate=RATE)
        try:
            time.sleep(1.5)                       # let both sessions come up
            source.start(lambda pcm: chunks.append(pcm.copy()))
            time.sleep(CAPTURE_S)
        finally:
            source.stop()
            for player in players:
                player.kill()
                player.wait(timeout=5)

    pcm = np.concatenate(chunks) if chunks else np.zeros((0, 2), np.int16)
    target, other = power_at(pcm, TARGET_HZ), power_at(pcm, OTHER_HZ)
    print(f"captured {len(pcm)} frames ({len(pcm) / CAPTURE_S:.0f}/s, "
          f"{pcm.dtype}) from pid {players[0].pid}")
    print(f"  target process   {TARGET_HZ} Hz : {target:8.2f}")
    print(f"  the other app    {OTHER_HZ} Hz : {other:8.2f}")

    if pcm.dtype != np.int16:
        print("FAIL — the sink speaks int16; this path delivered "
              f"{pcm.dtype}")
        return 1
    if target < 10:
        print("FAIL — heard nothing from the target process. Deaf tap, or "
              "ffplay never started rendering.")
        return 1
    if other > target / 100:
        print("FAIL — the other app bled in. This is desktop-wide capture, "
              "not per-process (audio_mode would read 'system').")
        return 1
    print("PASS — only the target process was captured.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
