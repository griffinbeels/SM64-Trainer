"""Bounded worker diagnostics; never called by renderer or audio callbacks."""

import time


# Native control.h / gpu_delivery.h reasons, including their distinct namespaces.
# Preserve raw numbers in callers: an older/newer DLL can have an unknown code.
_CONTROL = {
    0: "no cause supplied", 1: "no backend", 2: "owner gone",
    3: "lease expired", 4: "control protocol error", 5: "invalid GPU request",
    6: "backend admission failed", 7: "fresh request required",
    8: "native request invalid", 9: "native resources exhausted",
    10: "native admission system error",
}
_DELIVERY = dict(enumerate((
    "cancelled", "bad source", "bad request", "context unavailable",
    "context changed", "GL unsupported", "snapshot failed", "sample failed",
    "bridge failed", "channel failed", "source gap (legacy, branch unknown)",
    "owner gone", "deadline", "quarantined resources", "stamp table failed",
    "counter exhausted", "source image missing", "source record invalid",
    "source stamp count mismatch", "source stamp length mismatch",
    "source rejected by client", "source epoch changed", "source admission refused",
    # The ghost copy's own reasons: only ever in the twin status, never the
    # channel's terminal reason.
    "ghost copy snapshot failed", "ghost copy texture import failed",
    "ghost copy bridge failed", "ghost copy key abandoned",
    "capture layer predates ghost copies",
), 10000))

# Appended native delivery reasons remain readable before a picture channel
# exists. Generic GL errors stay transient; only positive missing capabilities
# establish this process cannot use the installed transport.
_GL_UNSUPPORTED = {
    10028: "OpenGL 4.5 is required for GPU recording",
    10029: "GPU recording needs GL_EXT_memory_object",
    10030: "GPU recording needs GL_EXT_memory_object_win32",
    10031: "GPU recording needs GL_EXT_win32_keyed_mutex",
    10032: "a required GPU recording OpenGL entry point is unavailable",
    10033: "a required GPU snapshot OpenGL entry point is unavailable",
}
_DELIVERY.update(_GL_UNSUPPORTED)
_DELIVERY.update({10034: "capture startup had no qualified source surface",
                  10035: "renderer capture gateway remained unavailable"})


def unsupported_capture_reason(reason):
    return _GL_UNSUPPORTED.get(reason)


# The bindings cause (plugin/gfxwrap/source_refusal.h): why the renderer's
# bindings were false at a BINDINGS refusal. Native values are appended, never
# renumbered; an unknown value stays unknown.
_BINDINGS_SITES = {
    1: ("framebuffer_target", "the renderer bound a framebuffer to a target capture does not track"),
    2: ("framebuffer_delete", "the renderer deleted more framebuffers at once than capture can follow"),
    3: ("texture_unit", "the renderer selected a texture unit outside its context's range"),
    4: ("vertex_array_delete", "the renderer deleted vertex arrays with an invalid count"),
    5: ("drawable_changed", "the window was resized from another thread"),
    6: ("drawable_committed", "the window's drawing area was missing or empty after a resize"),
    7: ("dispatch_reinstalled", "the renderer installed its GL function table twice"),
    8: ("dispatch_incomplete", "the driver lacks a framebuffer or texture-unit function"),
    9: ("dispatch_unarmed", "the renderer installed its GL function table before its context was ready"),
    10: ("dispatch_not_current", "the renderer installed its GL function table with another context current"),
    11: ("read_buffer", "the renderer selected a read buffer capture cannot certify"),
    12: ("texture_delete", "the renderer deleted more textures at once than capture can follow"),
    13: ("renderer_error", "the renderer's own OpenGL error check found an error"),
    14: ("gateway_open_error", "an OpenGL error was already pending when a capture step began"),
    15: ("gateway_close_error", "an OpenGL error followed a capture step's own work"),
    16: ("layer_fatal_error", "the ghost layer ran out of video memory or lost its context"),
    17: ("layer_undrained", "the ghost layer left OpenGL errors it could not clear"),
    18: ("created_not_current", "the renderer created its context without making it current"),
    19: ("created_exhausted", "the renderer created more contexts than capture can count"),
    20: ("created_no_units", "the driver reported no texture units"),
}
_BINDINGS_FLAGS = (
    "reported", "context_created", "practice_rom", "armed", "install_called", "dispatch_installed",
    "nominal", "owner_thread", "context_lost", "state_identity", "read_known", "default_read_known",
    "unit_known", "texture_known",
)
_GL_ERRORS = {0x500: "GL_INVALID_ENUM", 0x501: "GL_INVALID_VALUE", 0x502: "GL_INVALID_OPERATION",
              0x503: "GL_STACK_OVERFLOW", 0x504: "GL_STACK_UNDERFLOW", 0x505: "GL_OUT_OF_MEMORY",
              0x506: "GL_INVALID_FRAMEBUFFER_OPERATION", 0x507: "GL_CONTEXT_LOST"}
_IDENTITY_SOURCES = {1: "context_created", 2: "first_refusal"}


def bindings_flag_names(flags):
    """The set flags, in native bit order; unknown bits are not named."""
    return [name for bit, name in enumerate(_BINDINGS_FLAGS) if flags >> bit & 1]


def bindings_site_name(site):
    return _BINDINGS_SITES.get(site, ("unknown", None))[0] if site else None


def bindings_cause(site, gl_error, flags):
    """One plain sentence naming why the bindings were false, or None when the
    renderer reported nothing (an older renderer, or no BINDINGS refusal)."""
    if not flags & 1:
        return None
    named = set(bindings_flag_names(flags))
    if site:
        text = _BINDINGS_SITES.get(site, (None, f"an invalidation site this build does not know ({site})"))[1]
        if gl_error:
            text += f" ({_GL_ERRORS.get(gl_error, hex(gl_error))})"
        return text
    for flag, text in (
        ("context_created", "the renderer never reported creating its context for this ROM"),
        ("practice_rom", "the renderer did not recognize the ROM as a practice ROM"),
        ("armed", "the renderer's new context was never armed for capture"),
        ("install_called", "the renderer never installed its GL function table for this context"),
        ("dispatch_installed", "the renderer's GL function table belongs to an older context"),
        ("owner_thread", "capture ran on a thread other than the renderer's"),
        ("state_identity", "the renderer's state witness belongs to another context or window size"),
    ):
        if flag not in named:
            if flag == "context_created" and "context_lost" in named:
                return "the renderer lost its context and created no new one"
            return text
    if not {"read_known", "default_read_known", "unit_known", "texture_known"} <= named:
        return "the renderer's state witness lost track of a framebuffer or texture binding"
    return "the renderer reported no cause"


def context_identity_source(source):
    return _IDENTITY_SOURCES.get(source)


def native_reason(reason, *, channel=False):
    """Decode only known namespaces; channel Result 6 is not control reason 6."""
    if channel:
        namespace, code = reason & 0xF0000000, reason & 0x0FFFFFFF
        if namespace == 0x10000000:
            return f"channel result {code}"
        if namespace != 0x20000000:
            return "no cause supplied" if reason == 0 else "unknown channel reason"
        reason = code
    return _DELIVERY.get(reason, _CONTROL.get(reason, "unknown native reason"))


def failure_snapshot(exc, *, frontier, adapter, output):
    """Capture worker-owned counters before teardown drains or clears queues.

    This is the instant the server observes the fault, not necessarily the
    earlier native refusal. Status reads perform no channel calls or GPU work.
    An unavailable diagnostic must never hide the original capture exception.
    """
    result = dict(observed_unix_s=time.time(), frontier=frontier,
                  error=str(exc)[:512])
    from sm64_events.replay.gpuencoder import EncoderFailure, Result
    if isinstance(exc, EncoderFailure):
        code = exc.result
        result["encoder_failure"] = dict(
            operation=getattr(exc, "operation", None),
            # The existing protocol can synthesize STATE/QUARANTINE for
            # worker/controller faults. Do not call this a native return.
            result_namespace="encoder_protocol",
            result=int(code) if isinstance(code, int) else None,
            result_name=(Result(code).name if isinstance(code, int) and code in Result._value2member_map_ else None),
            native_status=exc.status,
            native_status_state="available" if exc.status is not None else "unavailable",
            worker_disposal_required=exc.worker_disposal_required,
            status_error=exc.status_error,
        )
    for name in ("state", "reason"):
        value = getattr(exc, name, None)
        if isinstance(value, int):
            result[f"channel_{name}"] = value
    for name, component in (("adapter", adapter), ("publication", output)):
        if component is None:
            continue
        try:
            result[name] = component.status()
        except Exception as status_error:  # noqa: BLE001 - diagnostic cannot replace capture failure.
            result[name] = {"unavailable": str(status_error)[:256]}
    return result


class CaptureTimings:
    """Fixed stage aggregates, retained on failure instead of per-frame logs.

    Scheduling/poll gaps are measured separately from executing media work.
    Storage is constant in capture duration; no raw pictures/packets are kept.
    """

    def __init__(self, clock=time.monotonic, wall_clock=time.time):
        self.clock = clock
        self.utc_offset = wall_clock() - clock()
        self.stages = {name: [0, 0.0, 0.0, 0.0] for name in (
            "channel", "encoder", "sink_prepare", "first_offer", "media_setup", "tick", "between_ticks",
            "audio_drain", "adapter_pump", "heartbeat", "media_drain", "media_age", "report",
            "mux_video", "mux_audio", "ledger_feed", "copy_lane",
        )}
        self.last_tick_end = None

    def measure(self, name, operation, *args, **kwargs):
        start = self.clock()
        if name == "tick" and self.last_tick_end is not None:
            self._record("between_ticks", start - self.last_tick_end, self.last_tick_end)
        try:
            return operation(*args, **kwargs)
        finally:
            end = self.clock()
            self._record(name, end - start, start)
            if name == "tick":
                self.last_tick_end = end

    def _record(self, name, elapsed, start):
        row = self.stages[name]
        row[0] += 1
        row[1] += elapsed
        if row[0] == 1 or elapsed > row[2]:
            row[2], row[3] = elapsed, start

    def summary(self):
        return {name: dict(count=n, total_ms=round(total * 1000, 3),
                           max_ms=round(peak * 1000, 3),
                           max_started_monotonic_s=round(start, 6),
                           max_started_unix_s=round(start + self.utc_offset, 6))
                for name, (n, total, peak, start) in self.stages.items() if n}
