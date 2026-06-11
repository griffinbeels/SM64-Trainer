"""Audio sources -> recorder AudioSource protocol.

Primary (wired in main.py): SystemAudioSource with PID TARGETING — it asks
Core Audio which render ENDPOINT hosts the target process's audio session
and loopback-captures THAT device. Live-audit finding (2026-06-11): the
machine's default output is "System (Elgato Wave:XLR)" but PJ64's session
lives on "Game (Elgato Wave:XLR)" (Wave Link virtual outputs) — capturing
the default endpoint recorded pure silence while the user heard the game
fine. Per-app endpoint routing makes "capture the default device" wrong.

proctap (ProcessAudioSource) is RETIRED from the wiring: per-process
loopback start()s successfully but delivers all-zero PCM on this machine —
it could not capture a beep played by its own process (false-healthy,
undetectable). Kept only for future re-evaluation.

proctap API notes (introspected from installed v0.x):
- AudioCallback = Callable[[bytes, int], None]  — (pcm_bytes, num_frames)
  num_frames is always -1 in the current implementation (TODO in source).
- on_data is passed to ProcessAudioCapture.__init__(), NOT to start().
- Output is always float32, 48000 Hz, 2-channel (stereo), values in [-1, 1]."""
import logging

import numpy as np

log = logging.getLogger("sm64.replay")


def device_name_hosting_pid(pid: int) -> str | None:
    """FriendlyName of the active render endpoint whose session list contains
    `pid`, else None. Pure Core Audio enumeration (pycaw/comtypes); the
    session→device mapping persists even while the app is silent."""
    try:
        import comtypes
        from comtypes import CLSCTX_ALL
        from pycaw.api.audiopolicy import IAudioSessionControl2, IAudioSessionManager2
        from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
        from pycaw.constants import CLSID_MMDeviceEnumerator
        from pycaw.pycaw import AudioUtilities

        devenum = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator, IMMDeviceEnumerator,
            comtypes.CLSCTX_INPROC_SERVER)
        coll = devenum.EnumAudioEndpoints(0, 1)  # eRender, DEVICE_STATE_ACTIVE
        for i in range(coll.GetCount()):
            dev = coll.Item(i)
            mgr = dev.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None)
            mgr = mgr.QueryInterface(IAudioSessionManager2)
            sess_enum = mgr.GetSessionEnumerator()
            for j in range(sess_enum.GetCount()):
                ctl2 = sess_enum.GetSession(j).QueryInterface(IAudioSessionControl2)
                try:
                    if ctl2.GetProcessId() == pid:
                        return AudioUtilities.CreateDevice(dev).FriendlyName
                except Exception:
                    continue
    except Exception:
        log.exception("audio session scan failed")
    return None


class PcmContinuity:
    """Wall-clock-locks a loopback stream. WASAPI loopback stops delivering
    packets while the endpoint is idle (no app rendering), which would
    silently PAUSE a cumulative-sample clock — every later chunk would be
    stamped earlier than it really happened, shearing clip audio against
    video. This guard tracks the expected sample position from QPC wall time
    and says how much silence to inject before a delivery to close the gap.
    Pure — unit-tested."""

    def __init__(self, rate: int, qpc_start_100ns: int):
        self._rate = rate
        self._t0 = qpc_start_100ns
        self._delivered = 0
        self._min_gap = rate // 10  # <100 ms is jitter, not idle

    def fill_before(self, qpc_now_100ns: int) -> int:
        expected = int((qpc_now_100ns - self._t0) / 1e7 * self._rate)
        gap = expected - self._delivered
        return gap if gap > self._min_gap else 0

    def on_delivered(self, n_samples: int) -> None:
        self._delivered += n_samples


def pick_loopback_device(loopback_devices: list[dict], target_name: str | None,
                         default_name: str) -> dict | None:
    """Choose the loopback entry matching the endpoint that hosts the target
    app's session; fall back to the default output. Pure — unit-tested.
    Loopback device names look like '<endpoint name> [Loopback]'."""
    def match(name):
        return next((d for d in loopback_devices if name and name in d["name"]),
                    None)
    return match(target_name) or match(default_name)


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
    """Device loopback capture. With a pid, captures the endpoint that
    actually HOSTS that process's audio session (per-app routing aware);
    without one, the default output."""

    mode = "system"

    def __init__(self, rate: int = 48000, pid: int | None = None):
        self._rate = rate
        self._pid = pid
        self._stream = None
        self._pa = None

    def start(self, on_pcm) -> None:
        import pyaudiowpatch as pyaudio

        target_name = device_name_hosting_pid(self._pid) if self._pid else None
        self._pa = pyaudio.PyAudio()
        try:
            wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            speakers = self._pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
            loopback = pick_loopback_device(
                list(self._pa.get_loopback_device_info_generator()),
                target_name, speakers["name"])
            if loopback is None:
                raise RuntimeError("no WASAPI loopback device matches the "
                                   "target or default output device")
            log.info("audio loopback endpoint: %s (app session endpoint: %s)",
                     loopback["name"], target_name or "unknown -> default")
            if int(loopback["defaultSampleRate"]) != self._rate:
                log.warning("loopback device rate %s != %s; recording at device "
                            "rate without resample (v1 limitation)",
                            loopback["defaultSampleRate"], self._rate)

            from sm64_events.replay.clock import qpc_100ns
            guard = PcmContinuity(self._rate, qpc_100ns())

            def cb(in_data, frame_count, time_info, status):
                fill = guard.fill_before(qpc_100ns())
                if fill:
                    log.info("audio idle gap: injecting %.1f s of silence to "
                             "keep the sample clock wall-true", fill / self._rate)
                    on_pcm(np.zeros((fill, 2), dtype=np.int16))
                    guard.on_delivered(fill)
                data = np.frombuffer(in_data, dtype=np.int16).reshape(-1, 2)
                on_pcm(data)
                guard.on_delivered(len(data))
                return (None, pyaudio.paContinue)

            self._stream = self._pa.open(
                format=pyaudio.paInt16, channels=2,
                rate=int(loopback["defaultSampleRate"]),
                input=True, input_device_index=loopback["index"],
                stream_callback=cb)
        except Exception:
            # The recorder only stop()s sources whose start() succeeded —
            # a partial failure here must release the PyAudio COM handle
            # itself or it leaks until GC.
            self._pa.terminate()
            self._pa = None
            raise

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None
