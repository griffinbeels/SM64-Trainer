"""The oracle reader's map-free rules (round 32 item 94).

The reader itself needs a clip and ffmpeg; what is pinned here is the part
that decides whether a reading may be believed WITHOUT a map -- the +1 rule
-- and how a map is scored against the oracle, because those two are what
turn "the screen says 12632" into a verdict.

AND the pixel path, since 2026-09-05: every test here was a pure-function
test, so when `replay/timerread.py` was deleted and took the three lazy
imports inside `register` with it, THE ORACLE was dead and this file was
green. The instrument that certifies every frame map cannot be covered only
where it is easiest to cover.
"""
from sm64_events.replay import oracleread as O


def test_plus_one_keeps_a_consistent_run_and_drops_the_misread():
    values = [100, 101, 102, 109, 104, 105, None, 107]
    out = O.enforce_plus_one(values)
    assert out.values == [100, 101, 102, None, 104, 105, None, 107]
    assert out.contradicted == 1
    assert out.read == 7          # the reader produced seven values
    assert out.consistent == 5    # 107 has no readable neighbour: kept, not vouched for


def test_plus_one_accepts_a_duplicate_picture_and_the_ram_delta_on_a_skipped_present():
    out = O.enforce_plus_one([10, 10, 11, 13, 14], ram_deltas=[None, 0, 1, 2, 1])
    assert out.values == [10, 10, 11, 13, 14] and out.contradicted == 0
    # No RAM witness: a value is vouched for by EITHER neighbour, so a lone
    # +2 step between two consistent pairs drops nothing -- 13 is vouched
    # for by 14 and 11 by 10. Only a value no neighbour agrees with goes.
    out = O.enforce_plus_one([10, 11, 13, 14])
    assert out.values == [10, 11, 13, 14] and out.contradicted == 0


def test_plus_one_drops_a_lone_backward_value():
    out = O.enforce_plus_one([50, 51, 12, 53])
    # 12 is contradicted by a credible witness (51, vouched for by 50); 53's
    # only witness is that stray, so 53 stays -- unvouched, not dropped
    assert out.values == [50, 51, None, 53] and out.contradicted == 1
    assert out.vouched_mask == [True, True, False, False]


def test_score_map_histograms_the_offset():
    reading = O.OracleReading(values=[5, 6, None, 8], read=3, consistent=3, contradicted=0)
    out = O.score_map(reading, [5, 7, 7, 8])
    assert out["exact"] == 2
    assert out["off_by"] == {0: 2, 1: 1}
    assert out["unreadable"] == 1 and out["slots"] == 4


def test_digits_assemble_left_aligned_until_the_first_blank():
    assert O.assemble(["1", "2", "6", "3", "2", None, None], [True] * 5 + [False, False]) == 12632
    # an unknown box before the blank poisons the whole value
    assert O.assemble(["1", None, "6"], [True, True, False]) is None
    # a blank first box is no number at all
    assert O.assemble([None, None], [False, False]) is None


def test_digit_count_guard_drops_truncated_reads_but_keeps_the_rollover():
    kept, dropped = O.digit_count_guard([12630, 12631, 1263, 12633, None, 126])
    assert kept == [12630, 12631, None, 12633, None, None] and dropped == 2
    kept, dropped = O.digit_count_guard([99998, 99999, 100000, 100001])
    assert kept == [99998, 99999, 100000, 100001] and dropped == 0


def test_duplicates_sees_through_compression_noise():
    import numpy as np
    base = np.full((67, 204, 3), 120, dtype=np.uint8)
    noisy = base.copy(); noisy[::7, ::5] += 3            # a re-encode of the same picture
    changed = base.copy(); changed[16:56, 20:54] = 250   # a digit changed
    assert O.duplicates(np.stack([base, noisy, changed])) == [False, True, False]


def test_the_shipped_reference_alphabet_holds_every_digit():
    table = O.load_reference()
    assert sorted(table) == list("0123456789")
    assert all(template.exemplars >= 8 for template in table.values())


def test_the_reader_can_actually_REGISTER_and_read_a_synthetic_row():
    """Drive the pixel path end to end on a row painted from the shipped
    alphabet itself: registration, box extraction, per-box distances, and
    assembly. No clip and no ffmpeg -- the cells are built here.

    This is the test whose absence let the oracle die silently: every other
    test in this file is a pure function over lists, and `register` /
    `_box` / `_box_score` reached none of them. Mutation proof: break any
    one of the three (a wrong BOX_H, a removed offset search) and this goes
    red where the rest stay green."""
    import numpy as np

    table = O.load_reference()
    # Paint "12345" into a blank row at the reader's own geometry, one
    # template per box, and give the last box the blank sky a five-digit
    # counter really has.
    height = O.ROW0 + O.BOX_H + 12
    width = O.BOX_X0 + O.PITCH * O.BOX_COUNT + O.BOX_W
    row = np.zeros((height, width, 3), dtype=np.uint8)
    for index, digit in enumerate("12345"):
        glyph = table[digit].median.astype(np.uint8)
        top, left = O.ROW0, O.BOX_X0 + O.PITCH * index
        row[top:top + O.BOX_H, left:left + O.BOX_W, :] = glyph
    cells = np.stack([row, row])

    offsets = O.register(cells, table)
    assert set(offsets) == {f"d{index}" for index in range(O.BOX_COUNT)}
    # Painted exactly where the reader looks, so it must find no shift at all.
    assert offsets["d0"] == (0, 0), offsets
    box = O._box(cells, offsets, "d0")
    assert box.shape[1:3] == (O.BOX_H, O.BOX_W), box.shape
    # And the box it cut is the glyph that was painted there.
    names, distances = O._distances(box.astype(np.float32), table)
    assert names[int(distances[0].argmin())].split("~")[0] == "1", (
        names[int(distances[0].argmin())])
