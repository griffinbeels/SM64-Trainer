"""Recorder-owned source selection: the GPU capture route, else the desktop
grab that hands over once the route is discoverable (shared by status reads).

One shipped capture path since 2026-09-16: `discover` finds the wrapper's
control page and returns the GPU source; a failed or transient GPU read keeps
the bounded retry gate. A qualified channel-startup deadline switches to the
existing desktop camera after proved GPU retirement. Without a
discoverable backend the recorder photographs the desktop with a watcher that
ends itself when the backend appears, so the order he opens things in does
not matter.

One exception keeps the desktop for good: a producer whose adapter can never
record through the layer (gpuadapter.py: a software adapter or a
driver older than its encoder). Discovery declines it, and the desktop source
says why (`gpu_unsupported`) so setup can accept its pictures instead.
The separate `gpu_fallback` explains failed startup without judging hardware.
"""

import logging

from sm64_events.core.timefmt import GAME_FPS
from sm64_events.replay.capturecontrol import CAP_GPU, CLOSED
from sm64_events.replay.gpucapture import discover
from sm64_events.replay.gpuretry import RetryGate
from sm64_events.replay.ownedclose import CleanupPending, retain
from sm64_events.replay.pluginsource import DesktopUntilLayerPresents

log = logging.getLogger("sm64.replay")


class DesktopForUnsupportedGpu(DesktopUntilLayerPresents):
    """The desktop grab, kept while this Project64's adapter cannot record."""

    def __init__(self, desktop, reason: str):
        super().__init__(desktop, note=f"recording the desktop: {reason}")
        self.gpu_unsupported = reason


class DesktopAfterGpuStartup(DesktopUntilLayerPresents):
    """Keep footage after channel startup failed, without inventing stamps."""

    def __init__(self, desktop, reason: str, retry_gate, expected_pid):
        self._retry_gate, self._expected_pid = retry_gate, expected_pid
        self._pending_control = None
        super().__init__(desktop, note=reason, backend_ready=self._successor_ready)
        self.gpu_fallback = reason

    def _close_control(self):
        if self._pending_control is None:
            return
        try:
            self._pending_control.close()
        except CleanupPending as exc:
            self._pending_control = retain(self._pending_control, exc)
            raise
        self._pending_control = None

    def _successor_ready(self):
        # The active fallback must observe ROM close/open itself. Constructing
        # and discarding a GpuCapture here would own unclosed wake/timer handles.
        self._close_control()
        try:
            self._pending_control = self._retry_gate.control_factory()
        except CleanupPending as exc:
            self._pending_control = exc.owner
            raise
        try:
            status = self._pending_control.status()
        except CleanupPending as exc:
            self._pending_control = retain(self._pending_control, exc)
            raise
        finally:
            self._close_control()
        if status.producer_pid != self._expected_pid:
            return False
        identity = self._retry_gate.observe(status)
        return bool(status.state != CLOSED and status.rom_open and status.capabilities & CAP_GPU
                    and not self._retry_gate.fallback_for(identity)
                    and not self._retry_gate.unsupported_for(identity))

    def stop(self):
        try:
            super().stop()
        finally:
            # The watch owns its passive control until it has actually joined.
            if self._thread is None or not self._thread.is_alive():
                self._close_control()


class SourceFactory:
    def __init__(self, layout, cfg, desktop_factory):
        # `layout` is a Layout, or a zero-argument callable returning the
        # running build's: the picture stamps follow a ROM swap (main.py).
        self._layout, self.cfg = layout, cfg
        self.desktop_factory = desktop_factory
        self.retry_gate = RetryGate()

    @property
    def layout(self):
        return self._layout() if callable(self._layout) else self._layout

    def _desktop(self, win):
        return self.desktop_factory(win, fps=self.cfg.fps)

    def diagnostics(self):
        return self.retry_gate.diagnostics()

    def create(self, win):
        # Called only after the machine-wide recorder lock. Only a qualified
        # unsupported verdict or cleanly retired startup deadline keeps desktop.
        gpu = self._gpu(win)
        if gpu is not None:
            return gpu
        reason = self.retry_gate.unsupported_for(self.retry_gate.identity_for(win.pid))
        if reason is not None:
            return DesktopForUnsupportedGpu(self._desktop(win), reason)
        reason = self.retry_gate.fallback_for(self.retry_gate.identity_for(win.pid))
        if reason is not None:
            return DesktopAfterGpuStartup(self._desktop(win), reason, self.retry_gate, win.pid)
        return self._waiting_desktop(win)

    def _gpu(self, win):
        return discover(
            win.pid, self.layout, nominal_rate=GAME_FPS, retry_gate=self.retry_gate
        )

    def _waiting_desktop(self, win):
        return DesktopUntilLayerPresents(
            self._desktop(win),
            note="the capture layer is not delivering; recording the desktop by time",
            backend_ready=lambda: self._gpu(win) is not None,
        )
