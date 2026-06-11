"""Audio sources -> recorder AudioSource protocol.

Primary: proctap per-process WASAPI loopback (pid-scoped: ONLY PJ64 audio,
no Discord/music bleed), float32 48 kHz stereo -> converted to s16 here so
everything downstream speaks one PCM dialect.
Fallback: PyAudioWPatch device-wide loopback (all system audio) — wired by
main.py as the recorder's fallback_audio_factory.

proctap API notes (introspected from installed v0.x):
- AudioCallback = Callable[[bytes, int], None]  — (pcm_bytes, num_frames)
  num_frames is always -1 in the current implementation (TODO in source).
- on_data is passed to ProcessAudioCapture.__init__(), NOT to start().
  start() takes no arguments.
- Output is always float32, 48000 Hz, 2-channel (stereo), values in [-1, 1].

VERIFY (live gate, Task 15): exact callback cadence and first-callback
latency; device-loopback sample rate on this machine."""
import logging

import numpy as np

log = logging.getLogger("sm64.replay")


def f32_to_s16(pcm_f32: np.ndarray) -> np.ndarray:
    """float32 interleaved [-1,1] -> (n, 2) int16, clipped.

    Arithmetic: clip to [-1.0, 1.0], multiply by 32767.0, truncate to int16.
    -1.0 * 32767 = -32767 (NOT -32768 — int16 min is asymmetric but we
    multiply by 32767, not 32768, so the negative floor is -32767)."""
    flat = np.asarray(pcm_f32, dtype=np.float32).reshape(-1, 2)
    return (np.clip(flat, -1.0, 1.0) * 32767.0).astype(np.int16)


class ProcessAudioSource:
    mode = "process"

    def __init__(self, pid: int):
        self._pid = pid
        self._tap = None

    def start(self, on_pcm) -> None:
        import proctap

        def _on_data(pcm_bytes: bytes, num_frames: int) -> None:
            # pcm_bytes: raw float32 stereo 48kHz; num_frames is -1 (unused)
            on_pcm(f32_to_s16(np.frombuffer(pcm_bytes, dtype=np.float32)))

        # on_data is passed to __init__, not start(); start() takes no args
        self._tap = proctap.ProcessAudioCapture(pid=self._pid, on_data=_on_data)
        self._tap.start()

    def stop(self) -> None:
        if self._tap is not None:
            try:
                self._tap.stop()
            except Exception:
                log.exception("proctap stop failed")
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
        if int(loopback["defaultSampleRate"]) != self._rate:
            log.warning("loopback device rate %s != %s; recording at device "
                        "rate without resample (v1 limitation)",
                        loopback["defaultSampleRate"], self._rate)

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
