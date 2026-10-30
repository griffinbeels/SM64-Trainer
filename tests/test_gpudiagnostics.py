"""Failure diagnostics preserve layer identity and include interrupted work."""

import re
from pathlib import Path

import pytest
from types import SimpleNamespace as NS

from sm64_events.replay.gpuchannel import ChannelStopped, CLOSED
from sm64_events.replay import gpudiagnostics
from sm64_events.replay.gpudiagnostics import CaptureTimings, bindings_cause, bindings_flag_names, native_reason

ROOT = Path(__file__).resolve().parents[1]


def test_every_native_bindings_site_and_flag_has_its_python_name():
    """The renderer writes these values (source_refusal.h); Python names them by
    value. A native site or flag added without a name here fails this test."""
    header = (ROOT / "plugin/gfxwrap/source_refusal.h").read_text(encoding="utf-8")
    sites = header[header.index("enum rs_bindings_site"):header.index("enum rs_bindings_flag")]
    flags = header[header.index("enum rs_bindings_flag"):header.index("} rs_bindings_cause")]
    native_sites = {int(value): name.lower() for name, value in re.findall(r"RS_BINDINGS_(\w+)=(\d+)", sites)}
    assert native_sites.pop(0) == "site_none" and len(native_sites) == 20
    assert {site: gpudiagnostics.bindings_site_name(site) for site in native_sites} == native_sites
    native_flags = {int(value): name.lower() for name, value in re.findall(r"RS_BINDINGS_(\w+)=(\d+)u", flags)}
    assert bindings_flag_names(sum(native_flags)) == [native_flags[bit] for bit in sorted(native_flags)]
    assert all(value == 1 << n for n, value in enumerate(sorted(native_flags)))


CREATED, PRACTICE, ARMED, INSTALL, INSTALLED, OWNER, LOST, IDENTITY = 2, 4, 8, 16, 32, 128, 256, 512
KNOWN = 1024 | 2048 | 4096 | 8192


@pytest.mark.parametrize(("site", "error", "flags", "words"), [
    (14, 0x502, 1 | CREATED, "an OpenGL error was already pending when a capture step began (GL_INVALID_OPERATION)"),
    (15, 0x9999, 1, "an OpenGL error followed a capture step's own work (0x9999)"),
    (99, 0, 1, "an invalidation site this build does not know (99)"),
    (0, 0, 1, "the renderer never reported creating its context for this ROM"),
    (0, 0, 1 | LOST, "the renderer lost its context and created no new one"),
    (0, 0, 1 | CREATED, "the renderer did not recognize the ROM as a practice ROM"),
    (0, 0, 1 | CREATED | PRACTICE | ARMED, "the renderer never installed its GL function table for this context"),
    (0, 0, 1 | CREATED | PRACTICE | ARMED | INSTALL, "the renderer's GL function table belongs to an older context"),
    (0, 0, 1 | CREATED | PRACTICE | ARMED | INSTALL | INSTALLED, "capture ran on a thread other than the renderer's"),
    (0, 0, 1 | CREATED | PRACTICE | ARMED | INSTALL | INSTALLED | OWNER,
     "the renderer's state witness belongs to another context or window size"),
    (0, 0, 1 | CREATED | PRACTICE | ARMED | INSTALL | INSTALLED | OWNER | IDENTITY | 1024,
     "the renderer's state witness lost track of a framebuffer or texture binding"),
    (0, 0, 1 | CREATED | PRACTICE | ARMED | INSTALL | INSTALLED | OWNER | IDENTITY | KNOWN,
     "the renderer reported no cause"),
])
def test_bindings_cause_names_the_first_site_or_the_missing_arming_step(site, error, flags, words):
    assert bindings_cause(site, error, flags) == words


def test_unreported_bindings_cause_stays_unknown():
    assert bindings_cause(14, 0x502, 0) is None  # an older renderer's zeros, never a guess


def test_python_names_exactly_the_sites_the_renderer_rebuilds_from():
    """rs_bindings_site_recoverable decides; Python only describes. A site moved
    between the recoverable and structural lists natively fails this test."""
    header = (ROOT / "plugin/gfxwrap/source_refusal.h").read_text(encoding="utf-8")
    sites = header[header.index("enum rs_bindings_site"):header.index("enum rs_bindings_flag")]
    values = {name: int(value) for name, value in re.findall(r"RS_BINDINGS_(\w+)=(\d+)", sites)}
    body = header[header.index("inline bool rs_bindings_site_recoverable"):]
    body = body[:body.index("return true;")]
    native = {values[name] for name in re.findall(r"case RS_BINDINGS_(\w+):", body)}
    assert native == {site for site in range(1, 21) if gpudiagnostics.bindings_site_recoverable(site)}
    assert native == {1, 2, 3, 4, 11, 12, 13, 14, 15, 17}


def test_rebuilt_and_unrebuildable_witnesses_are_named_in_plain_words():
    STRUCTURAL, NOMINAL = 16384, 64
    assert bindings_cause(14, 0x505, 1 | CREATED | STRUCTURAL) == (
        "an OpenGL error was already pending when a capture step began (GL_OUT_OF_MEMORY); "
        "the renderer cannot rebuild its capture state in this context")
    rebuilt = 1 | CREATED | PRACTICE | ARMED | INSTALL | INSTALLED | NOMINAL | OWNER | IDENTITY | 1024 | 4096 | 8192
    assert bindings_cause(0, 0, rebuilt) == (
        "the window's read buffer was unknown: an offscreen framebuffer was bound for reading when it was rebuilt")
    words = gpudiagnostics.bindings_reconstruction(
        {"epoch": 3, "site": 14, "gl_error": 0x502, "attempts": 2, "successes": 1, "ms_since_created": 1250})
    assert words == ("recording recovered: the renderer rebuilt its capture state from the driver 1.2 s after its "
                     "context started, because an OpenGL error was already pending when a capture step began "
                     "(GL_INVALID_OPERATION) (rebuild 1 of 2 tries)")
    assert gpudiagnostics.bindings_reconstruction({"reason": 10034, "reconstruct_attempts": 3,
                                                   "reconstruct_successes": 1}) == (
        "the renderer rebuilt its capture state 1 of 3 times in its current context, and recording still failed")
    assert gpudiagnostics.bindings_reconstruction({"reason": 10034, "reconstruct_attempts": 0}) is None


def test_error_numbers_are_decoded_in_their_own_namespace():
    assert native_reason(6) == "backend admission failed"
    assert native_reason(8) == "native request invalid"
    assert native_reason(9) == "native resources exhausted"
    assert native_reason(10) == "native admission system error"
    assert native_reason(0x10000006, channel=True) == "channel result 6"
    assert native_reason(6, channel=True) == "unknown channel reason"
    assert native_reason(0x30002710, channel=True) == "unknown channel reason"
    assert native_reason(99999) == "unknown native reason"
    assert "legacy, branch unknown" in native_reason(10010)
    for code, name in ((10016, "source image missing"),
                       (10021, "source epoch changed"), (10022, "source admission refused")):
        error = ChannelStopped(CLOSED, 0x20000000 | code)
        assert name in str(error) and str(error.reason) in str(error)
        assert not error.cancelled
    assert ChannelStopped(CLOSED, 0x20002710).cancelled


def test_interrupted_tick_and_scheduling_gap_are_separate_and_storage_is_bounded():
    now = [0.0]
    timing = CaptureTimings(lambda: now[0], wall_clock=lambda: 1000)

    def work(duration, fail=False):
        now[0] += duration
        if fail:
            raise RuntimeError("original failure")
        return "result"

    assert timing.measure("media_setup", work, 0.050) == "result"
    timing.measure("tick", work, 0.001)
    now[0] += 0.200
    with pytest.raises(RuntimeError, match="original failure"):
        timing.measure("tick", work, 0.030, True)
    summary = timing.summary()
    assert summary["media_setup"]["total_ms"] == 50
    assert summary["tick"] == dict(count=2, total_ms=31, max_ms=30,
                                   max_started_monotonic_s=.251,
                                   max_started_unix_s=1000.251)
    assert summary["between_ticks"]["max_ms"] == 200
    stages = set(timing.stages)
    assert "sink_prepare" in stages
    for _ in range(10000):
        timing.measure("tick", work, 0.001)
    assert set(timing.stages) == stages
    assert timing.summary()["tick"]["count"] == 10002


def test_nested_stages_attribute_inclusive_peak_and_keep_original_error():
    now = [0.0]
    timing = CaptureTimings(lambda: now[0], wall_clock=lambda: 100)

    def slow(*, duration):
        now[0] += duration
        raise RuntimeError("media failed")

    def tick():
        now[0] += .002
        timing.measure("adapter_pump", slow, duration=.300)

    with pytest.raises(RuntimeError, match="media failed"):
        timing.measure("tick", tick)
    summary = timing.summary()
    assert summary["tick"]["max_ms"] == 302
    assert summary["adapter_pump"]["max_ms"] == 300
    assert summary["adapter_pump"]["max_started_unix_s"] == 100.002


@pytest.mark.parametrize("broken_status", [False, True])
def test_failure_counters_are_captured_before_reconcile_and_teardown(caplog, broken_status):
    from sm64_events.replay.gpucapture_session import CaptureSession
    from sm64_events.replay.gpudemand import DemandSnapshot
    from sm64_events.replay.gpusettings import GpuSettings

    pending = [61]
    actions = []
    fault = ChannelStopped(CLOSED, 0x20000000 | 10022)

    def counters():
        actions.append("snapshot")
        if broken_status:
            raise RuntimeError("status unavailable")
        return {"pending_blocks": pending[0]}

    def reconcile():
        actions.append("reconcile")
        pending[0] = 0
        return False

    def tick():
        raise fault

    session = CaptureSession(NS(settings=GpuSettings()), NS(
        snapshot=DemandSnapshot("active"), reconcile_lifecycle=reconcile,
    ))
    session._open_channel = lambda: True
    session._open_encoder = lambda: ()
    session._first_offers = lambda: (1,)
    session._create_media = lambda *_: None
    session._continue = lambda: True
    session.tick = tick
    session.close = lambda: actions.append("close")
    session.output = NS(status=counters)
    session.adapter = NS(status=lambda: {"pending": 4, "bridges": 2})
    with caplog.at_level("INFO", logger="sm64.replay"), pytest.raises(ChannelStopped) as caught:
        session.run()
    assert caught.value is fault
    record = next(r for r in caplog.records if "failure before cleanup" in r.msg)
    captured = record.args
    assert actions == ["snapshot", "reconcile", "close"]
    assert pending[0] == 0
    assert captured["channel_reason"] == fault.reason
    assert captured["adapter"] == {"pending": 4, "bridges": 2}
    assert captured["publication"] == (
        {"unavailable": "status unavailable"} if broken_status else {"pending_blocks": 61}
    )
