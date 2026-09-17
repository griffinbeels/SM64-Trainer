"""The pad reader: Usamune's input display, read cell by cell, pins the map.

`replay/padread.py` is an OFFLINE INSTRUMENT (since 2026-09-05): no shipped
code path reaches it, only `tools/score_pad_read.py`, which a human runs
against a clip. So what is guarded here is the instrument's own contract, not
its internals -- it reads a clip, refuses one whose display it cannot read,
ships a reference alphabet, and reports an agreement a human can trust. The
alignment, anchoring and picture-flag internals it uses on the way there were
covered test by test until 2026-09-17; those tests restated the code and were
removed rather than kept as a second copy of it.

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
    # And `nowhere` separates the two kinds of blame: a reading that matches
    # no frame ANYWHERE nearby is the reader's own misread, not a map error.
    for column in ("d1", "d2"):
        cell = reads[("y", column)]
        cell.names[10] = "9" if cell.names[10] != "9" else "8"
    assert P.score(reads, truth, pads).nowhere >= 1


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


def test_a_letter_beside_magnitude_zero_is_impossible_and_is_corrected():
    """THE READER'S OWN COMMONEST ERROR, and it was invisible because every
    accuracy number was scored against a map the reader itself aligned.

    Usamune prints one axis as either a bare `0` -- blank letter, `0`, blank
    -- or a direction letter beside a magnitude of 1..99. A letter beside
    magnitude 0 cannot happen: a zero has no direction. Measured map-free on
    his Shoot into the Wild Blue clip (6510, 2026-09-02): 563 of 6316
    axis-readings, 8.9%, were exactly that, because `ink_mask` counts any
    bright pixel as font ink, so a blank cell over a white sky is all "ink"
    and the silver U -- mostly white highlight -- matches it at distance 38.
    Enforcing the grammar took that clip from 80.8% to 99.2% agreement and
    cut its misreads from 595 to 17."""
    from sm64_events.replay.padread import CellReads, enforce_grammar

    def cell(names):
        count = len(names)
        return CellReads(list(names), np.zeros(count), np.full(count, 99.0),
                         np.array([name != "" for name in names]))

    # slot 0: "U0" -- impossible.     slot 1: "U84" -- fine.
    # slot 2: bare "0" already right. slot 3: "D0" with a stray second digit.
    reads = {
        ("y", "letter"): cell(["U", "U", "", "D"]),
        ("y", "d1"): cell(["0", "8", "0", "0"]),
        ("y", "d2"): cell(["", "4", "", "7"]),
        ("x", "letter"): cell(["", "", "", ""]),
        ("x", "d1"): cell(["", "", "", ""]),
        ("x", "d2"): cell(["", "", "", ""]),
    }
    corrected = enforce_grammar(reads)
    letter, d1, d2 = (reads[("y", col)] for col in ("letter", "d1", "d2"))
    assert corrected == 3, "the letter on slots 0 and 3, and slot 3's d2"
    assert not letter.known[0], "'U0' kept its impossible letter"
    assert not letter.known[3] and not d2.known[3]
    # A real reading is untouched, and so is a magnitude the reader is sure of.
    assert letter.known[1] and letter.names[1] == "U"
    assert d1.known[1] and d1.names[1] == "8"
    assert d2.known[1] and d2.names[1] == "4"
    assert d1.known[0] and d1.names[0] == "0", "the magnitude is the trusted half"
    # ...and the other direction: it must correct misreads, not shave real
    # ones, so a clip whose axis readings are all well formed is untouched.
    well_formed = {
        ("y", "letter"): cell(["U", "D", ""]),
        ("y", "d1"): cell(["8", "1", "0"]),
        ("y", "d2"): cell(["4", "", ""]),
        ("x", "letter"): cell(["R", "", "L"]),
        ("x", "d1"): cell(["7", "0", "2"]),
        ("x", "d2"): cell(["", "", ""]),
    }
    assert enforce_grammar(well_formed) == 0


def test_the_audit_skips_a_slot_no_picture_matched_instead_of_crashing():
    """His first three clips on the timer wiring (2026-09-04): the audit is
    scored against the map the caller HANDED IN, and a feed-log or timer map
    holds None where no picture matched the slot. `score` added an offset to
    that None and the whole pad reader died -- on every clip -- so the map
    fell to the legacy aligner, which shifted the pyramid two frames off."""
    from sm64_events.replay.padread import CellReads, score

    def cell(names):
        count = len(names)
        return CellReads(list(names), np.zeros(count), np.full(count, 99.0),
                         np.array([name != "" for name in names]))

    reads = {
        ("y", "letter"): cell(["U", "U", "U"]),
        ("y", "d1"): cell(["8", "8", "8"]),
        ("y", "d2"): cell(["4", "4", "4"]),
        ("x", "letter"): cell(["", "", ""]),
        ("x", "d1"): cell(["0", "0", "0"]),
        ("x", "d2"): cell(["", "", ""]),
    }
    pads = {10: (0, 84), 11: (0, 84)}
    verdict = score(reads, [10, None, 11], pads)
    assert verdict.sure == 2 and verdict.agree == 2
    assert verdict.disagreements == []
