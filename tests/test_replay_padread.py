"""The pad reader: Usamune's input display, read cell by cell, pins the map.

The world is synthetic but shaped like his: the HUD font paints each glyph
IDENTICALLY every time (so a glyph is a fixed bitmap in fixed ink colours),
the world behind it changes every frame, a video slot shows some game
frame, and the track knows the pad on every game frame. A reader that
cannot tell a cell's glyph must say UNKNOWN, never guess; an aligned map
must agree with every cell that read; and the meter that reports the
agreement must go red when the map is put back out of step.
"""
import numpy as np

from sm64_events.replay import padread as P

FIRST = 5000


def _shape(glyph: str, h: int, w: int) -> np.ndarray:
    """A fixed, glyph-specific blob -- the font's bitmap for that glyph."""
    seed = sum(ord(c) * 31 ** i for i, c in enumerate(glyph)) + h * 7 + w
    rng = np.random.default_rng(seed)
    mask = np.zeros((h, w), bool)
    for _ in range(6):
        y, x = rng.integers(4, h - 10), rng.integers(4, w - 10)
        mask[y:y + rng.integers(6, 12), x:x + rng.integers(6, 12)] = True
    return mask


INK = {"digit": (235, 90, 20), "U": (205, 205, 215), "D": (60, 140, 235),
       "R": (60, 140, 235), "L": (240, 200, 40)}


def paint_cell(glyph: str, h: int, w: int, rng) -> np.ndarray:
    """A background nobody's font wears (mid greens and browns), with the
    glyph's bitmap painted over it in its ink colour."""
    base = np.array([rng.integers(60, 140), rng.integers(90, 170), rng.integers(40, 110)])
    cell = np.broadcast_to(base, (h, w, 3)).astype(np.int16)
    cell = cell + rng.integers(-15, 15, size=(h, w, 3))
    if glyph:
        colour = INK["digit"] if glyph.isdigit() else INK[glyph]
        cell[_shape(glyph, h, w)] = colour
    return np.clip(cell, 0, 255).astype(np.uint8)


def stick_at(frame: int) -> tuple[int, int]:
    """A stick that moves every frame -- the readable case -- with a short
    rest every so often, so the bare '0' is taught too."""
    if frame % 37 < 3:
        return 0, 0
    held = frame // 3                                # each value held 3 frames
    return ((held * 13) % 150) - 75, 84 - (held * 7) % 90


def cells_for(frames, pads, seed=11) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.zeros((len(frames), P.CELL_H, P.CELL_W, 3), np.uint8)
    for k, frame in enumerate(frames):
        pad = pads.get(frame)
        for row, (r0, r1) in P.ROWS.items():
            glyphs = (P.glyphs_of(pad[1] if row == "y" else pad[0], row)
                      if pad is not None else ("", "", ""))
            for ci, (col, (c0, c1)) in enumerate(P.COLS.items()):
                out[k, r0:r1, c0:c1] = paint_cell(glyphs[ci], r1 - r0, c1 - c0, rng)
    return out


def world(slots=600, pads=None):
    """(truth map, pads, cells): 30 game frames a second over 60 slots."""
    truth = [FIRST + k // 2 for k in range(slots)]
    if pads is None:
        pads = {f: stick_at(f) for f in range(FIRST - 40, FIRST + slots // 2 + 40)}
    return truth, pads, cells_for(truth, pads)


def alphabet_from(truth, pads, cells):
    return P.learn(cells, P.labels_from(truth, pads, hold=0))


# -- what the display prints ------------------------------------------------------
def test_glyphs_follow_usamune_s_own_layout():
    assert P.glyphs_of(0, "y") == ("", "0", "")
    assert P.glyphs_of(7, "y") == ("U", "7", "")
    assert P.glyphs_of(-84, "y") == ("D", "8", "4")
    assert P.glyphs_of(70, "x") == ("R", "7", "0")
    assert P.glyphs_of(-5, "x") == ("L", "5", "")


# -- reading one cell -----------------------------------------------------------------
def test_a_learned_glyph_reads_back_on_a_fresh_background_by_a_clear_margin():
    truth, pads, cells = world()
    alphabet = alphabet_from(truth, pads, cells)
    assert len(alphabet[("y", "d1")]) >= 9, "the moving stick shows every digit"
    fresh = cells_for(truth, pads, seed=99)         # same glyphs, new worlds
    reads = P.read(fresh, alphabet)
    for key in P.KEYS:
        cell = reads[key]
        want = P.labels_from(truth, pads, hold=0)[key]
        painted = [k for k, g in enumerate(want) if g]
        known = [k for k in painted if cell.known[k]]
        assert len(known) >= 0.97 * len(painted), f"{key}: painted cells must read"
        assert all(cell.names[k] == want[k] for k in known), f"{key}: a read is RIGHT"


def test_a_blank_cell_is_unknown_never_a_glyph():
    """No blank class exists: a cell either matches a painted glyph or it
    says nothing. So a resting stick's empty letter cell can never be read
    as U by a template that has no evidence against it."""
    truth, pads, cells = world()
    alphabet = alphabet_from(truth, pads, cells)
    rest = {f: (0, 0) for f in pads}                # every frame at rest
    quiet = cells_for(truth, rest, seed=5)
    reads = P.read(quiet, alphabet)
    assert not reads[("y", "letter")].known.any()
    assert not reads[("y", "d2")].known.any()
    assert reads[("y", "d1")].known.all() and set(reads[("y", "d1")].names) == {"0"}


def test_an_occluded_cell_is_unknown_rather_than_wrong():
    truth, pads, cells = world()
    alphabet = alphabet_from(truth, pads, cells)
    covered = cells.copy()
    covered[:, :, :, :] = 128                         # a flat wash over everything
    reads = P.read(covered, alphabet)
    assert not any(cell.known.any() for cell in reads.values())


# -- aligning the map ---------------------------------------------------------------
def test_a_prior_three_frames_out_is_pulled_onto_the_display_everywhere_it_reads():
    truth, pads, cells = world()
    alphabet = alphabet_from(truth, pads, cells)
    reads = P.read(cells, alphabet)
    prior = [f + 3 for f in truth]
    path = P.align(reads, prior, pads)
    assert path == truth


def test_a_wandering_prior_with_holes_is_still_corrected():
    truth, pads, cells = world(slots=900)
    alphabet = alphabet_from(truth, pads, cells)
    reads = P.read(cells, alphabet)
    prior = [f + (5 if k < 300 else (0 if k < 600 else -2)) for k, f in enumerate(truth)]
    for k in range(0, 900, 17):
        prior[k] = None
    path = P.align(reads, prior, pads)
    # Without picture flags, which of two slots inside one held value is
    # the frame edge is not knowable from the display -- so the contract
    # is the pad SHOWN, exact on every slot, and the frame within one.
    assert all(pads[p] == pads[t] for p, t in zip(path, truth))
    assert max(abs(p - t) for p, t in zip(path, truth)) <= 1
    assert sum(p != t for p, t in zip(path, truth)) < 900 * 0.05


def test_duplicated_slots_dwell_and_new_pictures_advance():
    """The quantiser's law, kept: every slot of one picture answers the
    same frame, and a new picture is a new frame."""
    truth, pads, cells = world()
    alphabet = alphabet_from(truth, pads, cells)
    reads = P.read(cells, alphabet)
    same = np.array([k % 2 == 1 for k in range(len(truth))])
    changed = ~same
    changed[0] = False
    path = P.align(reads, [f + 2 for f in truth], pads, same, changed)
    assert path == truth
    steps = np.diff(path)
    assert set(steps[same[1:]]) == {0} and set(steps[changed[1:]]) == {1}


def test_an_unreadable_stretch_keeps_the_prior_s_own_steps():
    """Where the display says nothing, the path follows the step the
    clocks took between the same two slots -- a dropped frame the ledger
    saw stays dropped -- instead of drifting to a rate of its own."""
    slots = 600
    truth = [FIRST + k // 2 + (2 if k >= 300 else 0) for k in range(slots)]
    pads = {f: stick_at(f) for f in range(FIRST - 40, FIRST + slots // 2 + 40)}
    cells = cells_for(truth, pads)                   # the capture really dropped two frames
    alphabet = alphabet_from(truth, pads, cells)
    reads = P.read(cells, alphabet)
    for cell in reads.values():
        cell.known[200:400] = False                  # ...inside a stretch the display hides
    path = P.align(reads, truth, pads)               # the clocks' map carries the drop
    assert path == truth, "the drop the clocks saw is kept where they saw it"
    smooth = [FIRST + k // 2 for k in range(slots)]  # a prior that never saw the drop
    path = P.align(reads, smooth, pads)
    assert path[:200] == truth[:200] and path[400:] == truth[400:]
    assert all(b - a in (0, 1, 2) for a, b in zip(path, path[1:]))


# -- the meter --------------------------------------------------------------------------
def test_the_verdict_counts_agreement_and_names_what_matches_nothing():
    truth, pads, cells = world()
    alphabet = alphabet_from(truth, pads, cells)
    reads = P.read(cells, alphabet)
    verdict = P.score(reads, truth, pads)
    assert verdict.sure > 500 and verdict.agree == verdict.sure
    assert verdict.nowhere == 0 and verdict.agreement == 1.0
    # Put the map six frames out of step: the meter must go red, and every
    # disagreement names the slot, the row, what was read and what the map
    # claims -- the list he checks a clip by.
    wrong = P.score(reads, [f + 6 for f in truth], pads)
    assert wrong.agree < wrong.sure * 0.2
    assert wrong.disagreements and wrong.disagreements[0][1] in ("y", "x")


def test_a_read_that_matches_no_frame_nearby_is_a_misread_not_a_map_error():
    truth, pads, cells = world()
    alphabet = alphabet_from(truth, pads, cells)
    reads = P.read(cells, alphabet)
    reads[("y", "d1")].names[10] = "9" if reads[("y", "d1")].names[10] != "9" else "8"
    reads[("y", "d2")].names[10] = "9" if reads[("y", "d2")].names[10] != "9" else "8"
    verdict = P.score(reads, truth, pads)
    assert verdict.nowhere >= 1


# -- one clip, end to end ----------------------------------------------------------------
def test_read_clip_pins_a_clip_to_its_display_and_reports_what_it_learned(monkeypatch):
    truth, pads, cells = world()
    monkeypatch.setattr(P, "decode_cells", lambda ffmpeg, clip: cells)
    monkeypatch.setattr(P, "picture_flags", lambda ffmpeg, clip, c: (None, None))
    reference = alphabet_from(truth, pads, cells_for(truth, pads, seed=77))
    reading = P.read_clip("clip.mp4", [f + 4 for f in truth], pads, "ffmpeg",
                          reference=reference)
    assert reading is not None
    assert reading.frame_map == truth
    assert reading.verdict.agreement == 1.0 and reading.verdict.nowhere == 0
    assert "7" in reading.learned[("y", "d1")], "the clip taught itself its digits"


def test_read_clip_refuses_a_clip_whose_display_it_cannot_read(monkeypatch):
    truth, pads, cells = world()
    noise = np.random.default_rng(1).integers(0, 255, size=cells.shape, dtype=np.uint8)
    monkeypatch.setattr(P, "decode_cells", lambda ffmpeg, clip: noise)
    monkeypatch.setattr(P, "picture_flags", lambda ffmpeg, clip, c: (None, None))
    reference = alphabet_from(truth, pads, cells)
    assert P.read_clip("clip.mp4", truth, pads, "ffmpeg", reference=reference) is None
    assert P.read_clip("clip.mp4", [None] * len(truth), pads, "ffmpeg",
                       reference=reference) is None


def test_the_reference_alphabet_ships_with_the_package():
    alphabet = P.load_alphabet()
    assert set(alphabet[("y", "d1")]) >= set("0123456789")
    assert set(alphabet[("y", "letter")]) == {"U", "D"}
    assert set(alphabet[("x", "letter")]) == {"R", "L"}
    for table in alphabet.values():
        for template in table.values():
            assert template.weight.sum() >= P.MIN_WEIGHT


def test_the_lit_icon_count_pins_a_press_while_the_stick_rests():
    """His report 2026-09-01: a C-down pressed on the frame after a reset
    showed up one frame late -- the stick was at rest, so the digits said
    nothing there and the map kept the clocks' answer. Usamune lights one
    icon per held button, on/off with the pad (measured, no fade), so the
    COUNT of lit icons per picture is evidence with no template at all."""
    slots = 600
    truth = [FIRST + k // 2 for k in range(slots)]
    pads = {f: (0, 0) for f in range(FIRST - 40, FIRST + slots // 2 + 40)}   # resting throughout
    A_BIT = 0x8000
    held = {f: (A_BIT if FIRST + 120 <= f < FIRST + 140 else 0) for f in pads}   # one press, held 20 frames
    cells = cells_for(truth, pads)
    alphabet = alphabet_from(truth, pads, cells)
    reads = P.read(cells, alphabet)
    icons = [({A_BIT} if held[f] else set()) for f in truth]                      # what the strip shows lit
    prior = [f + 2 for f in truth]                                            # the clocks, two frames out
    blind = P.align(reads, prior, pads)
    assert blind[240:280] != truth[240:280], "with the stick at rest the digits cannot correct it"
    seeing = P.align(reads, prior, pads, held=held, icons=icons)
    assert seeing[220:300] == truth[220:300], "the press and release edges pin the frames around them"


def test_the_reset_s_white_flash_pins_the_reset_frame():
    """His frames 0-7 on 5534: through the white flash and the resting fall
    after it nothing is readable, and the map stayed a frame behind. The
    first white picture is the spawn frame the journal recorded (measured
    there, the C-down on the first faded-in picture confirming it), so it
    anchors the path where nothing else can."""
    slots = 600
    truth = [FIRST + k // 2 for k in range(slots)]
    pads = {f: (0, 0) for f in range(FIRST - 40, FIRST + slots // 2 + 40)}
    cells = cells_for(truth, pads)
    cells[300:306] = 250                              # three white frames from the reset
    reset = truth[300]
    anchors = P.flash_anchors(cells, [f + 2 for f in truth], [reset])
    assert anchors == {300: reset}, anchors
    reads = P.read(cells, alphabet_from(truth, pads, cells))
    blind = P.align(reads, [f + 2 for f in truth], pads)
    assert blind[300] != reset
    pinned = P.align(reads, [f + 2 for f in truth], pads, anchors=anchors)
    assert pinned[300] == reset and pinned[280:340] == truth[280:340]
    assert P.flash_anchors(cells, [f + 2 for f in truth], []) == {}
    assert P.flash_anchors(cells, [f + 40 for f in truth], [reset]) == {}, "a reset far from the prior is not this flash"


def test_every_slot_after_a_pictures_first_is_flagged_as_the_same_picture(monkeypatch):
    """picture_runs yields (first slot, LENGTH). Unpacking it as (start, end)
    made the flag `same[start + 1 : end + 1]` -- for a run of two at slot 100
    that is `same[101:3]`, EMPTY -- so the "this picture is held, the map may
    not advance" flag reached almost nothing and the aligner walked straight
    across duplicated pictures (his pyramid clip: the map stepped +1 over 110
    of its 115 held frames, 2026-09-02). Every slot after a run's first is the
    same picture, wherever the run sits."""
    import pathlib

    from sm64_events.replay import mapalign, padread

    runs = [(0, 1), (1, 3), (4, 1), (5, 2), (7, 1)]   # 8 slots, 5 pictures
    monkeypatch.setattr(mapalign, "decode_grey", lambda ffmpeg, clip: np.zeros((8, 4), np.uint8))
    monkeypatch.setattr(mapalign, "picture_runs", lambda grey: runs)
    cells = np.zeros((8, padread.CELL_H, padread.CELL_W, 3), np.uint8)
    same, _changed = padread.picture_flags("ffmpeg", pathlib.Path("nope.mp4"), cells)
    assert list(same) == [False, False, True, True, False, False, True, False]
    # Mutation proof: the (start, end) reading marks almost nothing.
    wrong = np.zeros(8, bool)
    for start, end in runs:
        wrong[start + 1:min(end + 1, 8)] = True
    assert list(wrong) != list(same)
