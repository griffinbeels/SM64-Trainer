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


def session_peak_for_pid(pid: int) -> float:
    """Instantaneous meter peak of the pid's audio session (0.0-1.0), or
    -1.0 when no session exists. Used by the silence watchdog to tell
    'the app is quiet' apart from 'our loopback stream went deaf'."""
    try:
        import comtypes
        from comtypes import CLSCTX_ALL
        from pycaw.api.audiopolicy import (IAudioSessionControl2,
                                           IAudioSessionManager2)
        from pycaw.api.endpointvolume import IAudioMeterInformation
        from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
        from pycaw.constants import CLSID_MMDeviceEnumerator

        devenum = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator, IMMDeviceEnumerator,
            comtypes.CLSCTX_INPROC_SERVER)
        coll = devenum.EnumAudioEndpoints(0, 1)
        for i in range(coll.GetCount()):
            dev = coll.Item(i)
            mgr = dev.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None)
            mgr = mgr.QueryInterface(IAudioSessionManager2)
            se = mgr.GetSessionEnumerator()
            for j in range(se.GetCount()):
                ctl = se.GetSession(j)
                try:
                    if ctl.QueryInterface(IAudioSessionControl2).GetProcessId() != pid:
                        continue
                except Exception:
                    continue
                meter = ctl.QueryInterface(IAudioMeterInformation)
                return float(meter.GetPeakValue())
    except Exception:
        log.exception("session peak probe failed")
    return -1.0


class SystemAudioSource:
    """Device loopback capture with SELF-HEALING. With a pid, captures the
    endpoint that actually HOSTS that process's audio session (per-app
    routing aware); without one, the default output.

    Watchdog: a WASAPI loopback stream goes silently deaf when the world
    changes under it — the target app restarts (new session), the endpoint
    re-enumerates (Wave Link restart leaves a zombie device with an
    IDENTICAL name), or routing moves. No error is ever raised; the stream
    just delivers dither forever (live: PJ64 restarted mid-session, its new
    session was ACTIVE at peak 0.35 on the Game endpoint while our stream
    recorded silence). Every few seconds the watchdog compares 'has the
    stream heard anything loud?' against the session's own meter; sustained
    deafness while the app is audibly emitting triggers a full re-resolve
    and stream reopen. The pump persists across reopens; ffmpeg's wall-clock
    stamping + aresample bridge the reopen gap so the timeline stays
    continuous."""

    mode = "system"

    _DEAF_AFTER_S = 5.0
    _CHECK_EVERY_S = 2.0

    def __init__(self, rate: int = 48000, pid: int | None = None):
        self._rate = rate
        self._pid = pid
        self._stream = None
        self._pa = None
        self._pump = None
        self._watchdog = None
        self._stop_evt = None

    def _open_stream(self) -> None:
        """(Re)resolve the endpoint and open the loopback stream feeding the
        existing pump. Raises on failure; caller handles cleanup/retry."""
        import pyaudiowpatch as pyaudio

        target_name = device_name_hosting_pid(self._pid) if self._pid else None
        if self._pa is None:
            self._pa = pyaudio.PyAudio()
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
        pump_feed = self._pump.feed

        # REAL-TIME RULE: this callback runs on PortAudio's thread with a
        # ~21 ms buffer behind it. It must NEVER touch the recorder lock,
        # the writer, the disk, or logging — any stall drops packets
        # (measured 6% sustained loss with the old in-callback work).
        def cb(in_data, frame_count, time_info, status):
            pump_feed(in_data, status)
            return (None, pyaudio.paContinue)

        self._stream = self._pa.open(
            format=pyaudio.paInt16, channels=2,
            rate=int(loopback["defaultSampleRate"]),
            input=True, input_device_index=loopback["index"],
            stream_callback=cb)

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                log.exception("loopback stream close failed")
            self._stream = None
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None

    def start(self, on_pcm) -> None:
        import threading

        from sm64_events.replay._system_audio import AudioPump

        # The pump is a pure RT-safe handoff now (no wall-clock epoch): it
        # forwards device PCM straight to on_pcm and the single ffmpeg mux
        # stamps + aresample-locks it. No audio origin to align here.
        self._pump = AudioPump(self._rate, on_pcm)
        try:
            self._open_stream()
        except Exception:
            # The recorder only stop()s sources whose start() succeeded —
            # release everything ourselves on partial failure.
            self._pump.stop()
            self._pump = None
            self._close_stream()
            raise

        self._stop_evt = threading.Event()
        self._watchdog = threading.Thread(
            target=self._watch, name="audio-watchdog", daemon=True)
        self._watchdog.start()

    def _watch(self) -> None:
        import time as _time
        while not self._stop_evt.wait(self._CHECK_EVERY_S):
            heard = _time.monotonic() - self._pump.last_loud_t
            if heard < self._DEAF_AFTER_S:
                continue
            if self._pid is None:
                continue
            peak = session_peak_for_pid(self._pid)
            if peak < 0.02:
                continue  # app genuinely quiet (or gone) — nothing to heal
            log.warning("audio watchdog: stream deaf %.0f s while the app's "
                        "session peaks at %.2f — reopening loopback", heard, peak)
            try:
                self._close_stream()
                self._open_stream()
                self._pump.last_loud_t = _time.monotonic()  # grace period
            except Exception:
                log.exception("loopback reopen failed — will retry")

    def stop(self) -> None:
        if self._stop_evt is not None:
            self._stop_evt.set()
        if self._watchdog is not None:
            self._watchdog.join(timeout=5)
            self._watchdog = None
        self._close_stream()
        if self._pump is not None:
            self._pump.stop()
            self._pump = None
