"""Audio sources -> recorder AudioSource protocol.

Primary (wired in main.py): ProcessAudioSource — WASAPI *process* loopback
via proctap, which taps only the target process's own render stream. A
replay therefore carries the game and nothing else: no Discord call, no
music, no browser tab. That is the whole point of preferring it (user
report 2026-07-30: "only capture the audio of the game and not of the
entire desktop… very convenient for uploading xcams that don't have calls
and streams and music in the background").

Fallback: SystemAudioSource, DEVICE loopback with PID TARGETING — it asks
Core Audio which render ENDPOINT hosts the target process's audio session
and loopback-captures THAT device. Live-audit finding (2026-06-11): the
machine's default output is "System (Elgato Wave:XLR)" but PJ64's session
lives on "Game (Elgato Wave:XLR)" (Wave Link virtual outputs) — capturing
the default endpoint recorded pure silence while the user heard the game
fine. Per-app endpoint routing makes "capture the default device" wrong.
It records whatever else shares that endpoint, which is the bug above; it
stays as the fallback because no audio at all is worse, and `audio_mode`
in /api/replay/status says which one is live.

proctap was RETIRED on 2026-06-11 for delivering all-zero PCM ("could not
capture a beep played by its own process"). That verdict was wrong, and
the way it was reached is the lesson: a `winsound.Beep` is emitted by the
kernel's beep path, not by the calling process's audio session, so process
loopback is *correct* to return silence for it — the test could not pass.
Re-measured 2026-07-31 with two ffplay children playing 440 Hz and 880 Hz
simultaneously, capturing only the 440 Hz process: 440 Hz present at
exactly the amplitude device loopback saw, 880 Hz at literally zero, full
48 kHz delivery. Device loopback on the same machine heard both at 1.0x.
Prove an audio path with a tone whose SOURCE PROCESS you chose.

proctap API notes (installed 1.0.3):
- AudioCallback = Callable[[bytes, int], None]  — (pcm_bytes, num_frames)
  num_frames is always -1 in the current implementation (TODO in source).
- on_data is passed to ProcessAudioCapture.__init__(), NOT to start().
- Output is always float32, 48000 Hz, 2-channel (stereo), values in [-1, 1],
  delivered CONTINUOUSLY — silence arrives as zeros rather than as a gap,
  unlike device loopback, which delivers nothing while the endpoint idles."""
import logging
import threading
import time

import numpy as np

from sm64_events.replay._system_audio import AudioPump

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


class DeafStreamWatchdog:
    """Content-liveness guard shared by both capture paths.

    Every capture path here can go silently deaf: no error is ever raised,
    the stream simply delivers dither forever. Device loopback does it when
    the world changes under it — the target app restarts (new session), the
    endpoint re-enumerates (a Wave Link restart leaves a zombie device with
    an IDENTICAL name), or routing moves (live: PJ64 restarted mid-session,
    its new session ACTIVE at peak 0.35 on the Game endpoint while our
    stream recorded silence). So STATUS proves nothing and CONTENT proves
    everything: every couple of seconds, compare 'has the stream heard
    anything loud?' against the pid's own session meter, and reopen when the
    app is audibly emitting into silence on our side.

    Reopening keeps the pump, so ffmpeg's wall-clock stamping + aresample
    bridge the gap and the timeline stays continuous."""

    def __init__(self, pid: int | None, heard_at, reopen, label: str,
                 deaf_after_s: float = 5.0, check_every_s: float = 2.0):
        self._pid = pid
        self._heard_at = heard_at        # () -> monotonic time of last loud pkt
        self._reopen = reopen            # () -> None, raises on failure
        self._label = label
        self._deaf_after_s = deaf_after_s
        self._check_every_s = check_every_s
        self._grace_t = 0.0
        self._stop_evt = threading.Event()
        self._thread = threading.Thread(
            target=self._watch, name="audio-watchdog", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _watch(self) -> None:
        while not self._stop_evt.wait(self._check_every_s):
            heard = time.monotonic() - max(self._heard_at(), self._grace_t)
            if heard < self._deaf_after_s or self._pid is None:
                continue
            peak = session_peak_for_pid(self._pid)
            if peak < 0.02:
                continue  # app genuinely quiet (or gone) — nothing to heal
            log.warning("audio watchdog: %s deaf %.0f s while the app's "
                        "session peaks at %.2f — reopening",
                        self._label, heard, peak)
            try:
                self._reopen()
                self._grace_t = time.monotonic()
            except Exception:
                log.exception("%s reopen failed — will retry", self._label)

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)


class ProcessAudioSource:
    """Per-process WASAPI loopback (proctap): captures ONLY the target
    process's render stream, so nothing else playing on the machine can
    bleed into a replay. THE primary source — see the module docstring for
    the measurement that says it works and the bad test that once retired
    it.

    proctap hands PCM to us on its own reader thread, not on a WASAPI
    real-time callback, but it still must not block: the same AudioPump
    stands between it and the recorder, so a wedged writer drops packets
    instead of stalling the tap."""

    mode = "process"

    def __init__(self, pid: int, rate: int = 48000):
        self._pid = pid
        self._rate = rate
        self._tap = None
        self._pump = None
        self._watchdog = None

    def _open_tap(self) -> None:
        """(Re)open the per-process tap feeding the existing pump. Raises on
        failure; caller handles cleanup/retry."""
        import proctap

        pump_feed = self._pump.feed

        def _on_data(pcm_bytes: bytes, num_frames: int) -> None:
            # pcm_bytes: raw float32 stereo 48kHz; num_frames is -1 (unused).
            # The pump speaks int16 — the one format on the wire to the sink —
            # so convert here, off both the recorder and the native drain.
            pump_feed(f32_to_s16(
                np.frombuffer(pcm_bytes, dtype=np.float32)).tobytes(), 0)

        # on_data is passed to __init__, not start(); start() takes no args.
        # proctap also queues every chunk into an internal 100-slot async
        # queue nobody drains in callback mode; it fills once and then drops,
        # so it costs a bounded ~400 KB and never grows.
        # Assign BEFORE start() so a tap that constructs and then fails to
        # start is still ours to release.
        self._tap = proctap.ProcessAudioCapture(pid=self._pid, on_data=_on_data)
        self._tap.start()
        log.info("audio: per-process loopback attached to pid %d", self._pid)

    def _close_tap(self) -> None:
        if self._tap is not None:
            try:
                self._tap.stop()
            except Exception:
                log.exception("proctap stop failed")
            self._tap = None

    def start(self, on_pcm) -> None:
        self._pump = AudioPump(self._rate, on_pcm)
        try:
            self._open_tap()
        except Exception:
            # The recorder only stop()s sources whose start() succeeded —
            # release everything ourselves on partial failure.
            self._pump.stop()
            self._pump = None
            self._close_tap()
            raise
        self._watchdog = DeafStreamWatchdog(
            self._pid, lambda: self._pump.last_loud_t,
            self._reopen, "process tap")
        self._watchdog.start()

    def _reopen(self) -> None:
        self._close_tap()
        self._open_tap()

    def stop(self) -> None:
        if self._watchdog is not None:
            self._watchdog.stop()
            self._watchdog = None
        self._close_tap()
        if self._pump is not None:
            self._pump.stop()
            self._pump = None


class SystemAudioSource:
    """Device loopback capture — the FALLBACK, used when the per-process tap
    cannot start. With a pid, captures the endpoint that actually HOSTS that
    process's audio session (per-app routing aware); without one, the default
    output. Everything else sharing that endpoint lands in the recording,
    which is why it is no longer primary. Self-heals via DeafStreamWatchdog."""

    mode = "system"

    def __init__(self, rate: int = 48000, pid: int | None = None):
        self._rate = rate
        self._pid = pid
        self._stream = None
        self._pa = None
        self._pump = None
        self._watchdog = None

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
        self._watchdog = DeafStreamWatchdog(
            self._pid, lambda: self._pump.last_loud_t,
            self._reopen, "device loopback")
        self._watchdog.start()

    def _reopen(self) -> None:
        self._close_stream()
        self._open_stream()

    def stop(self) -> None:
        if self._watchdog is not None:
            self._watchdog.stop()
            self._watchdog = None
        self._close_stream()
        if self._pump is not None:
            self._pump.stop()
            self._pump = None
