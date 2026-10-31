"""What the renderer's ghost draw path did, as report fields and one sentence.

The renderer records, from values it already holds, the first step that
refused the latest display list and the latest picture, and the GLideN64
settings those steps read (plugin/gfxwrap/ghost_draw_facts.h, the page's
`draw` block). An AMD tester's reports (2026-10-30) showed playback READY,
three clips prepared and no ghost on screen, and nothing said why.

`fields` flattens one block into `draw_*` values for the native status, which
the Debug report and automatic reports carry (`diagnostic_privacy`). Never
"unknown = fine": every field is always present, and a value the renderer did
not report reads "unknown", whether the renderer is older (no draw facts), the
block belongs to another generation, or that group was never recorded.
"""
from __future__ import annotations

from sm64_events.ghost import native_layout as L

UNKNOWN = "unknown"
#: Every report key `fields` returns, in report order.
FIELDS = (
    "draw_refusal", "draw_list_gate", "draw_present_gate", "draw_lists", "draw_presents",
    "draw_observing", "draw_faulted", "draw_compositor", "draw_model", "draw_planned",
    "draw_create_step", "draw_prepare_result", "draw_batch_result", "draw_finalize_result",
    "draw_last_fault", "draw_fault_site", "draw_fault_gl_error",
    "draw_observed_width", "draw_observed_height", "draw_prepared_width", "draw_prepared_height",
    "draw_presentation_ready", "draw_presentation_faulted", "draw_ready_step",
    "draw_fbe", "draw_fxaa", "draw_overscan", "draw_blit_workaround", "draw_n64_depth_compare",
    "draw_fragment_depth_write", "draw_multisampling", "draw_rdram_size", "draw_rdram_supported",
    "draw_evaluations", "draw_playback_refusals",
)
#: GLideN64's RDRAMSize values the ghost reads (8 and 4 MiB): ghost_link.cpp's rule.
READABLE_RDRAM = (0x7FFFFF, 0x3FFFFF)

_LIST_WHY = {
    "unknown": "the renderer did not say why",
    "no_context": "the renderer had no graphics context",
    "not_requested": "the trainer was not asking the renderer for Mario's poses",
    "bindings": "the renderer's record of its graphics state was refused and could not be rebuilt",
    "rdram_size": "GLideN64 reported an RDRAM size the ghost cannot read",
    "not_observed": "the pose reader refused the frame (no capture set up for this cartridge)",
    "other_thread": "another renderer thread owns ghost playback",
    "no_clean_consumer": "the capture plugin did not agree to clean presentation",
    "no_identity": "the renderer's graphics context could not be identified",
    "no_dispatch": "the ghost's OpenGL functions could not be loaded",
    "create_scope": "the ghost's setup was refused by a pending graphics error",
    "no_compositor": "the ghost drawer could not be created for this graphics context",
    "faulted": "the ghost stepped aside for this graphics context after a graphics error",
    "no_model": "playback did not hold Mario's model",
    "no_extent": "the game's 3D picture size had not been seen yet",
    "prepare_scope": "preparing Mario's model was refused by a pending graphics error",
    "prepare": "the ghost drawer could not prepare Mario's model",
    "no_sample": "no pose was read for this frame",
    "no_plan": "playback chose no ghost for this frame",
    "no_seal": "the frame ended before the game's 3D pass finished",
    "seal_world": "the 3D pass did not match the prepared picture size or context",
    "seal_buffer": "the game's 3D pass drew to another buffer",
    "no_pose": "playback posed no ghost at the end of the 3D pass",
    "seal_scope": "drawing the ghost was refused by a pending graphics error",
    "batch": "the ghost drawer refused to draw",
    "target_changed": "the game changed or cleared its picture after the ghost was drawn",
    "world_lost": "the game's 3D pass was lost after the ghost was drawn",
    "hud": "copying the game's HUD over the ghost was refused",
    "stale": "the frame ended early or the ghost command changed",
    "close_dirty": "a graphics error happened while the ghost was drawn",
    "finalize": "the ghost drawer could not finish the picture",
}
_PRESENT_WHY = {
    "unknown": "the renderer never offered it a picture to go in",
    "no_clean_consumer": "the capture plugin did not agree to clean presentation",
    "no_presentation": "the ghost's picture step was never set up for this context",
    "no_provider": "the ghost drawer never offered its picture",
    "identity": "the graphics context or window changed",
    "split": "the game's picture is split across two buffers",
    "overscan": "GLideN64's Overscan setting is on",
    "multisampling": "GLideN64's multisampling (MSAA) setting is on",
    "fxaa": "GLideN64's FXAA setting is on",
    "blit_workaround": "GLideN64's blit screen workaround is on",
    "source": "the game's picture is not a plain single-sample texture",
    "source_extent": "the processed picture size differs from the game's buffer",
    "no_image": "no finished ghost image matched this picture",
    "no_display_buffer": "the screen route had no ghost display buffer",
    "extent": "the presentation could not size its surfaces",
    "not_ready": "the presentation surfaces were not ready",
    "repeat": "the picture already carried a ghost",
    "faulted": "the presentation stepped aside after a graphics error",
    "image": "the ghost image did not match the presentation surfaces",
    "srgb": "sRGB writes were on",
    "copy": "copying the clean picture failed",
    "draw": "drawing the ghost into the picture failed",
}
# Why the compositor or the presentation could not be set up, as a short tail.
_CREATE_WHY = {"refused": "the driver lacks OpenGL 4.3 or texture units",
               "scope": "a graphics error was pending", "dirty": "setup raised a graphics error",
               "dispatch": "OpenGL functions were missing"}
_READY_WHY = {"no_clean_consumer": "clean presentation was not agreed", "no_identity": "no graphics context",
              "source_format": "the window's pixel format was never seen", "dispatch": "OpenGL functions were missing",
              "scope": "a graphics error was pending", "create": "the driver lacks OpenGL 4.3 or draw buffers",
              "gamma": "the gamma shader failed", "dirty": "setup raised a graphics error",
              "extent": "the window size could not be read"}


def _name(names: tuple, code: int) -> str:
    return names[code] if 0 <= code < len(names) else f"code_{code}"


def _result(code: int) -> str:
    """A compositor call's result (Result + 1); 0 means not called this context."""
    return "not_called" if code == 0 else _name(L.COMPOSITOR_RESULTS, code - 1)


def fields(draw) -> dict:
    """Every `draw_*` report field from one decoded block (None: none readable)."""
    out = dict.fromkeys(FIELDS, UNKNOWN)
    if draw is not None:
        known, flags = draw.known, draw.flags
        # Gates and steps carry their own unknown: code 0.
        out["draw_list_gate"] = _name(L.DRAW_LIST_GATES, draw.list_gate)
        out["draw_present_gate"] = _name(L.DRAW_PRESENT_GATES, draw.present_gate)
        out["draw_create_step"] = _name(L.DRAW_CREATE_STEPS, draw.create_step)
        out["draw_ready_step"] = _name(L.DRAW_READY_STEPS, draw.ready_step)
        if known & L.DRAW_KNOWN_LIST:
            out.update(draw_lists=draw.lists, draw_observing=bool(flags & L.DRAW_OBSERVING),
                       draw_faulted=bool(flags & L.DRAW_FAULTED), draw_compositor=bool(flags & L.DRAW_COMPOSITOR),
                       draw_model=bool(flags & L.DRAW_MODEL), draw_planned=bool(flags & L.DRAW_PLANNED),
                       draw_prepare_result=_result(draw.prepare_result), draw_batch_result=_result(draw.batch_result),
                       draw_finalize_result=_result(draw.finalize_result),
                       draw_last_fault=_name(L.DRAW_FAULT_STEPS, draw.fault_step),
                       draw_observed_width=draw.observed_width, draw_observed_height=draw.observed_height,
                       draw_prepared_width=draw.prepared_width, draw_prepared_height=draw.prepared_height)
            if draw.fault_step:
                out.update(draw_fault_site=draw.fault_site, draw_fault_gl_error=draw.fault_error)
        if known & L.DRAW_KNOWN_PRESENT:
            out["draw_presents"] = draw.presents
        if known & L.DRAW_KNOWN_SWAP:
            out.update(draw_presentation_ready=bool(flags & L.DRAW_PRESENTATION),
                       draw_presentation_faulted=bool(flags & L.DRAW_PRESENTATION_FAULTED))
        if known & L.DRAW_KNOWN_SETTINGS:
            settings = draw.settings
            out.update(draw_fbe=bool(settings & L.DRAW_SETTING_FBE), draw_fxaa=bool(settings & L.DRAW_SETTING_FXAA),
                       draw_overscan=bool(settings & L.DRAW_SETTING_OVERSCAN),
                       draw_blit_workaround=bool(settings & L.DRAW_SETTING_BLIT_WORKAROUND),
                       draw_n64_depth_compare=bool(settings & L.DRAW_SETTING_N64_DEPTH_COMPARE),
                       draw_fragment_depth_write=bool(settings & L.DRAW_SETTING_FRAGMENT_DEPTH_WRITE),
                       draw_multisampling=draw.multisampling, draw_rdram_size=draw.rdram_size,
                       draw_rdram_supported=draw.rdram_size in READABLE_RDRAM)
        if known & L.DRAW_KNOWN_PLAYBACK:
            out.update(draw_evaluations=draw.evaluations, draw_playback_refusals=draw.playback_refusals)
    out["draw_refusal"] = sentence(out)
    return out


def sentence(facts: dict) -> str:
    """One plain sentence for the Debug report: what stopped the ghost, if anything."""
    gate, shown = facts.get("draw_list_gate", UNKNOWN), facts.get("draw_present_gate", UNKNOWN)
    if gate == UNKNOWN and shown == UNKNOWN:
        return "The renderer did not report its ghost drawing steps (an older renderer, or no frame yet)."
    if gate == "drew":
        if shown == "shown":
            return "The renderer drew the ghost and put it in the picture."
        return f"The ghost was drawn but not shown: {_present(facts, shown)}."
    return f"No ghost was drawn: {_list(facts, gate)}."


def _list(facts: dict, gate: str) -> str:
    why = _LIST_WHY.get(gate, f"step {gate}")
    if gate == "rdram_size" and type(facts.get("draw_rdram_size")) is int:
        return f"{why} ({facts['draw_rdram_size']:#x})"
    if gate == "no_compositor" and facts.get("draw_create_step") in _CREATE_WHY:
        return f"{why}; {_CREATE_WHY[facts['draw_create_step']]}"
    if gate in ("faulted", "close_dirty") and type(facts.get("draw_fault_gl_error")) is int:
        return f"{why} (GL error {facts['draw_fault_gl_error']:#06x} in {facts.get('draw_last_fault')})"
    if gate in ("prepare", "batch", "finalize"):
        result = facts.get(f"draw_{gate}_result")
        if gate == "batch" and result == "unsupported" and facts.get("draw_multisampling") not in (0, UNKNOWN):
            return f"{why}: multisampling (MSAA) is on"
        if result not in (None, UNKNOWN):
            return f"{why} ({result})"
    return why


def _present(facts: dict, shown: str) -> str:
    why = _PRESENT_WHY.get(shown, f"step {shown}")
    if shown == "no_presentation" and facts.get("draw_ready_step") in _READY_WHY:
        return f"{why}; {_READY_WHY[facts['draw_ready_step']]}"
    return why


def timeline(facts: dict) -> str:
    """The `sm64.ghost.state` line's short form: list gate, then picture gate."""
    return f"{facts.get('draw_list_gate', UNKNOWN)}/{facts.get('draw_present_gate', UNKNOWN)}"
