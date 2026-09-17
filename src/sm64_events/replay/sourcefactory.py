"""Recorder-owned source selection: the GPU capture route, else the desktop
grab that hands over once the route is discoverable (shared by status reads).

One shipped capture path since 2026-09-16: `discover` finds the wrapper's
control page and returns the GPU source; a failed or transient GPU read keeps
the bounded retry gate and never activates a raw pixel path. Without a
discoverable backend the recorder photographs the desktop with a watcher that
ends itself when the backend appears, so the order he opens things in does
not matter.
"""

import logging

from sm64_events.core.timefmt import GAME_FPS
from sm64_events.replay.gpucapture import discover
from sm64_events.replay.gpuretry import RetryGate

log = logging.getLogger("sm64.replay")


class SourceFactory:
    def __init__(self, layout, cfg, desktop_factory):
        self.layout, self.cfg = layout, cfg
        self.desktop_factory = desktop_factory
        self.retry_gate = RetryGate()

    def _desktop(self, win):
        return self.desktop_factory(win, fps=self.cfg.fps)

    def create(self, win):
        # Called only after the machine-wide recorder lock. GPU failure retains
        # this backend and its bounded retry gate, never activates raw fallback.
        gpu = self._gpu(win)
        if gpu is not None:
            return gpu
        return self._waiting_desktop(win)

    def _gpu(self, win):
        return discover(
            win.pid, self.layout, nominal_rate=GAME_FPS, retry_gate=self.retry_gate
        )

    def _waiting_desktop(self, win):
        from sm64_events.replay.pluginsource import DesktopUntilLayerPresents

        return DesktopUntilLayerPresents(
            self._desktop(win),
            note="the capture layer is not delivering; recording the desktop by time",
            backend_ready=lambda: self._gpu(win) is not None,
        )
