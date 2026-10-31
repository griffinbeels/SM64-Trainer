"""The renderer's draw path facts, from its page to the Debug report.

An AMD tester (RX 9070 XT, stock Project64 1.6, JP ROM) never saw ghost Mario
while every report read playback READY, three clips prepared and nothing
displayed (2026-10-30): the renderer's draw path refused silently. The renderer
now records which step refused the latest list and picture
(plugin/gfxwrap/ghost_draw_facts.h; native tests prove each gate). These tests
cover the trainer's half: decoding the page, reading an older renderer as
unknown, the sentence each candidate cause prints, the timeline line and the
automatic report schema. Never "unknown = fine".
"""
import ctypes as C
import time

import pytest

from sm64_events.core.diagnostic_privacy import GHOST_DRAW_FIELDS, GHOST_HOOK_FIELDS, safe_snapshot
from sm64_events.ghost import capture, draw_facts, native_channel as channel, native_layout as L, native_protocol as P
from sm64_events.memory.ghost_layout import GhostLayout
from sm64_events.memory.layout import US
from tests.test_ghost_native_channel import kernel, status  # noqa: F401 - the channel's fake page
from tests.test_ghost_native_session import Capture, native_session
from tests.test_ghost_session import drive

GATE = {name: code for code, name in enumerate(L.DRAW_LIST_GATES)}
PRESENT = {name: code for code, name in enumerate(L.DRAW_PRESENT_GATES)}
READY = {name: code for code, name in enumerate(L.DRAW_READY_STEPS)}
CREATE = {name: code for code, name in enumerate(L.DRAW_CREATE_STEPS)}
FAULT = {name: code for code, name in enumerate(L.DRAW_FAULT_STEPS)}
RESULT = {name: code + 1 for code, name in enumerate(L.COMPOSITOR_RESULTS)}
ALL_KNOWN = (L.DRAW_KNOWN_LIST | L.DRAW_KNOWN_SETTINGS | L.DRAW_KNOWN_PRESENT | L.DRAW_KNOWN_PLAYBACK
             | L.DRAW_KNOWN_SWAP)
FBE = L.DRAW_SETTING_FBE


def block(list_gate, present_gate="no_image", **values):
    """The tester's machine as each candidate leaves it: FBE on, 8 MiB RDRAM,
    a 1920x1080 world, compositor and model ready unless the case says not."""
    draw = P.Draw(version=L.DRAW_VERSION, generation=1, known=ALL_KNOWN,
                  flags=L.DRAW_OBSERVING | L.DRAW_COMPOSITOR | L.DRAW_MODEL | L.DRAW_PLANNED | L.DRAW_PRESENTATION,
                  list_gate=GATE[list_gate], lists=900, present_gate=PRESENT[present_gate], presents=900,
                  ready_step=READY["ready"], create_step=CREATE["created"], prepare_result=RESULT["ok"],
                  observed_width=1920, observed_height=1080, prepared_width=1920, prepared_height=1080,
                  settings=FBE, rdram_size=0x7FFFFF, evaluations=0, playback_refusals=0)
    for name, value in values.items():
        setattr(draw, name, value)
    return draw


# What a report from the tester's machine would print for each live candidate.
CANDIDATES = {
    "A: ghost_draw stepped aside after a dirty creation scope": (
        block("faulted", flags=L.DRAW_OBSERVING | L.DRAW_FAULTED, create_step=CREATE["dirty"],
              fault_step=FAULT["create"], fault_site=15, fault_error=0x500),
        "No ghost was drawn: the ghost stepped aside for this graphics context after a graphics error "
        "(GL error 0x0500 in create)."),
    "A: compositor creation refused, never retried": (
        block("no_compositor", flags=L.DRAW_OBSERVING, create_step=CREATE["refused"], prepare_result=0),
        "No ghost was drawn: the ghost drawer could not be created for this graphics context; "
        "the driver lacks OpenGL 4.3 or texture units."),
    "A: the presentation's context_ready failed into context_stopping": (
        block("drew", "no_presentation", flags=L.DRAW_OBSERVING | L.DRAW_COMPOSITOR | L.DRAW_MODEL | L.DRAW_PLANNED,
              ready_step=READY["dirty"]),
        "The ghost was drawn but not shown: the ghost's picture step was never set up for this context; "
        "setup raised a graphics error."),
    "B: the compositor's allocate returned resources": (
        block("prepare", flags=L.DRAW_OBSERVING | L.DRAW_COMPOSITOR | L.DRAW_PRESENTATION,
              prepare_result=RESULT["resources"]),
        "No ghost was drawn: the ghost drawer could not prepare Mario's model (resources)."),
    "C: MSAA refuses the batch": (
        block("batch", multisampling=4, batch_result=RESULT["unsupported"]),
        "No ghost was drawn: the ghost drawer refused to draw: multisampling (MSAA) is on."),
    "C: FXAA refuses the picture": (
        block("drew", "fxaa", settings=FBE | L.DRAW_SETTING_FXAA),
        "The ghost was drawn but not shown: GLideN64's FXAA setting is on."),
    "C: overscan refuses the picture": (
        block("drew", "overscan", settings=FBE | L.DRAW_SETTING_OVERSCAN),
        "The ghost was drawn but not shown: GLideN64's Overscan setting is on."),
    "C: the blit workaround refuses the picture": (
        block("drew", "blit_workaround", settings=FBE | L.DRAW_SETTING_BLIT_WORKAROUND),
        "The ghost was drawn but not shown: GLideN64's blit screen workaround is on."),
    "F: GLideN64's RDRAMSize is neither 4 nor 8 MiB": (
        block("rdram_size", flags=L.DRAW_OBSERVING | L.DRAW_COMPOSITOR | L.DRAW_MODEL, rdram_size=0x7FFFEF),
        "No ghost was drawn: GLideN64 reported an RDRAM size the ghost cannot read (0x7fffef)."),
    "safe_boundary refused a thread change": (
        block("other_thread", flags=L.DRAW_OBSERVING | L.DRAW_COMPOSITOR),
        "No ghost was drawn: another renderer thread owns ghost playback."),
}


@pytest.mark.parametrize("case", list(CANDIDATES))
def test_each_candidate_cause_prints_its_own_sentence(case):
    draw, sentence = CANDIDATES[case]
    facts = draw_facts.fields(draw)
    assert facts["draw_refusal"] == sentence
    assert set(facts) == set(draw_facts.FIELDS)
    assert len(sentence) <= 140  # the report keeps 140 characters of a string


def test_candidate_f_and_the_settings_read_as_facts_not_guesses():
    rdram = draw_facts.fields(CANDIDATES["F: GLideN64's RDRAMSize is neither 4 nor 8 MiB"][0])
    assert (rdram["draw_rdram_size"], rdram["draw_rdram_supported"], rdram["draw_list_gate"]) == (
        0x7FFFEF, False, "rdram_size")
    msaa = draw_facts.fields(CANDIDATES["C: MSAA refuses the batch"][0])
    assert (msaa["draw_multisampling"], msaa["draw_batch_result"], msaa["draw_fbe"], msaa["draw_fxaa"]) == (
        4, "unsupported", True, False)
    faulted = draw_facts.fields(CANDIDATES["A: ghost_draw stepped aside after a dirty creation scope"][0])
    assert (faulted["draw_faulted"], faulted["draw_compositor"], faulted["draw_last_fault"],
            faulted["draw_fault_site"], faulted["draw_fault_gl_error"]) == (True, False, "create", 15, 0x500)
    assert draw_facts.timeline(faulted) == "faulted/no_image"


def test_an_older_renderer_and_unrecorded_groups_read_as_unknown_never_as_fine():
    older = draw_facts.fields(None)
    assert set(older.values()) == {draw_facts.UNKNOWN, older["draw_refusal"]}
    assert older["draw_refusal"] == ("The renderer did not report its ghost drawing steps "
                                     "(an older renderer, or no frame yet).")
    assert draw_facts.timeline(older) == "unknown/unknown"
    # A list ended, but no setting, picture or playback counter was recorded.
    partial = draw_facts.fields(P.Draw(version=L.DRAW_VERSION, generation=1, known=L.DRAW_KNOWN_LIST,
                                       list_gate=GATE["drew"], lists=3, flags=L.DRAW_COMPOSITOR))
    assert partial["draw_compositor"] is True and partial["draw_model"] is False
    assert all(partial[key] == draw_facts.UNKNOWN for key in (
        "draw_fbe", "draw_multisampling", "draw_rdram_size", "draw_rdram_supported", "draw_presentation_ready",
        "draw_ready_step", "draw_present_gate", "draw_evaluations"))
    assert partial["draw_refusal"] == ("The ghost was drawn but not shown: "
                                       "the renderer never offered it a picture to go in.")
    # A picture was offered but no swap has said whether the presentation is ready.
    offered = draw_facts.fields(P.Draw(version=L.DRAW_VERSION, generation=1, known=L.DRAW_KNOWN_PRESENT,
                                       present_gate=PRESENT["no_image"], presents=2, ready_step=READY["ready"]))
    assert (offered["draw_presents"], offered["draw_ready_step"], offered["draw_presentation_ready"]) == (
        2, "ready", draw_facts.UNKNOWN)


def test_every_sentence_fits_the_reports_string_limit():
    longest = {"draw_rdram_size": 0xFFFFFFFF, "draw_create_step": "refused", "draw_fault_gl_error": 0xFFFF,
               "draw_last_fault": "annotations", "draw_multisampling": 16, "draw_ready_step": "source_format"}
    sentences = [draw_facts.sentence({**longest, "draw_list_gate": gate, "draw_present_gate": "unknown",
                                      **{f"draw_{step}_result": "unsupported" for step in ("prepare", "batch", "finalize")}})
                 for gate in L.DRAW_LIST_GATES]
    sentences += [draw_facts.sentence({**longest, "draw_list_gate": "drew", "draw_present_gate": shown})
                  for shown in L.DRAW_PRESENT_GATES]
    sentences += [draw_facts.sentence({**longest, "draw_list_gate": "drew", "draw_present_gate": "no_presentation",
                                       "draw_ready_step": step}) for step in L.DRAW_READY_STEPS]
    assert max(map(len, sentences)) <= 140, max(sentences, key=len)
    names = (*L.DRAW_LIST_GATES, *L.DRAW_PRESENT_GATES)
    assert not any(f"step {name}." in text for text in sentences for name in names), "every code has its own words"


def test_the_channel_reads_this_generations_facts_and_an_older_renderers_as_unknown(kernel):
    connection = channel.NativeChannel(123, GhostLayout.from_layout(US).to_bytes())
    try:
        written = block("no_compositor")
        C.memmove(kernel.address + P.DRAW_AT, bytes(written), C.sizeof(written))
        connection.poll()
        assert connection.draw is None, "a renderer without GIPC_CAP_DRAW_FACTS says nothing"
        reply = status()
        reply.capabilities = L.IPC_CAP_DRAW_FACTS
        C.memmove(kernel.address, bytes(reply), 256)
        connection.poll()
        assert bytes(connection.draw) == bytes(written)
        written.generation = 2  # an earlier producer's copy is not this generation's
        C.memmove(kernel.address + P.DRAW_AT, bytes(written), C.sizeof(written))
        connection.poll()
        assert connection.draw is None
        C.memmove(kernel.address + P.DRAW_AT, bytes(C.sizeof(written)), C.sizeof(written))
        connection.poll()
        assert connection.draw is None, "a zero tail (version 0) is unknown"
    finally:
        connection.close()


def test_the_capture_status_carries_the_facts_and_the_latest_lists_hooks(monkeypatch):
    """The pump's report: what the Debug report and automatic reports read."""
    class Source:
        draw = block("prepare", prepare_result=RESULT["resources"])

        def __init__(self, *_):
            pass

        def poll(self, control=None):
            value = status()
            value.hook_list, value.hook_list_micros = 9, 812
            return value, ()

        def close(self):
            pass

    monkeypatch.setattr(capture, "NativeChannel", Source)
    source = capture.NativeCapture(US, lambda *_: None, producer_pid=123)
    source.start()
    try:
        row, deadline = {}, time.monotonic() + 3
        while time.monotonic() < deadline and not row.get("available"):
            row = source.snapshot_status()
            time.sleep(0.01)
    finally:
        assert source.stop(3)
    assert row["draw_list_gate"] == "prepare" and row["draw_prepare_result"] == "resources"
    assert row["hook_list_micros"] == 812
    assert row["draw_refusal"] == "No ghost was drawn: the ghost drawer could not prepare Mario's model (resources)."
    assert {f"hook_{name}" for name in capture.LATEST_HOOKS} <= set(row)
    assert (row["hook_open_micros"], row["hook_error_reads"], row["hook_ghosts"]) == (0, 0, 0)


def test_the_automatic_report_keeps_every_fact_and_hook_number():
    assert set(draw_facts.FIELDS) == set(GHOST_DRAW_FIELDS)
    assert {f"hook_{name}" for name in capture.LATEST_HOOKS} == set(GHOST_HOOK_FIELDS)
    native = {"available": True, "state": 4, "playback_reason": 2, "displayed": False,
              "hook_open_micros": 0, "hook_batch_micros": 0, "hook_error_reads": 0, "hook_ghosts": 0,
              "hook_list_micros": 812, "hook_close_micros": 0, "hook_finalize_micros": 0,
              **draw_facts.fields(CANDIDATES["A: compositor creation refused, never retried"][0])}
    kept = safe_snapshot({"ghost": {"native": native}})["ghost"]["native"]
    assert kept == native
    older = {"available": True, **draw_facts.fields(None)}
    assert safe_snapshot({"ghost": {"native": older}})["ghost"]["native"] == older


def test_the_ghost_timeline_names_the_renderers_first_refusal(tmp_path, caplog):
    session, source, _ = native_session(tmp_path)
    facts = draw_facts.fields(CANDIDATES["A: compositor creation refused, never retried"][0])
    source.snapshot_status = lambda *, blocking=True: {
        **Capture.snapshot_status(source), **facts, "observed_unix_s": time.time()}
    with caplog.at_level("INFO", logger="sm64.ghost.state"):
        drive(session)
        source.snapshot_status = lambda *, blocking=True: {
            **Capture.snapshot_status(source), **draw_facts.fields(None), "observed_unix_s": time.time()}
        drive(session)
    lines = [record.getMessage() for record in caplog.records if record.name == "sm64.ghost.state"
             and record.getMessage().startswith("ghost: ")]
    assert lines[-2].endswith(" displayed=False draw=no_compositor/no_image")
    assert lines[-1].endswith(" displayed=False draw=unknown/unknown")
