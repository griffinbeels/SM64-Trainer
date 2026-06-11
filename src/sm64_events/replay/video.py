"""windows-capture (WGC) adapter -> recorder VideoSource protocol.

Lazy import: constructing the recorder must never require capture hardware.
frame.timespan is WGC SystemRelativeTime — QPC 100 ns ticks, the same
timebase CaptureClock anchors against (clock.py).

frame_buffer.copy(): the library may reuse the underlying buffer between
callbacks, and the recorder holds the last frame for CFR gap fill — an
uncopied reference would mutate retroactively.

Event registration API note: windows_capture.WindowsCapture.event() checks
handler.__name__ — the decorated functions MUST be named on_frame_arrived
and on_closed exactly; arbitrary names raise ValueError.

on_frame_arrived receives (frame, capture_control) — two positional args.
The capture_control is the same CaptureControl returned by
start_free_threaded(); it is not used here (stop() is called via self._control).

VERIFY (live gate, Task 15): draw_border=False suppression of the yellow
capture border is UNVERIFIED per research — cosmetic either way."""
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
            on_frame(frame.frame_buffer.copy(), frame.timespan)

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
