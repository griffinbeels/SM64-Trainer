import pytest

from sm64_events.inputs.document import DocumentError, decode, encode
from sm64_events.inputs.frame import InputFrame
from sm64_events.memory import addresses as A


def frames(spec):
    """spec: list of (frame_number, buttons, stick_x, stick_y)."""
    return [(number, InputFrame(buttons, 0, stick_x, stick_y))
            for number, buttons, stick_x, stick_y in spec]


def a_document(spec, **kwargs):
    fields = dict(target="star WF 1", strategy="10 coin", version="us",
                  origin="attempt 40213")
    fields.update(kwargs)
    return encode(frames(spec), **fields)


def test_a_document_round_trips():
    original = frames([(0, 0, 58, 61), (1, 0x8000, 2, 79),
                       (2, 0x8000, 2, 79), (9, 0, 0, 0)])
    got = decode(encode(original, target="star WF 1", strategy=None,
                        version="us", origin="authored"))
    assert got.frames == original
    assert got.target == "star WF 1"
    assert got.strategy is None
    assert got.origin == "authored"


def test_frames_are_numbered_from_the_start_of_the_track():
    """A document is a run, not a slice of a session: it always starts at 0,
    so two of them lie on one axis without knowing when either was played."""
    text = a_document([(41000, 0x8000, 0, 0), (41001, 0x8000, 0, 0)])
    assert [number for number, _f in decode(text).frames] == [0, 1]


def test_a_held_run_writes_one_line_carrying_its_span():
    text = a_document([(number, 0x8000, 60, 0) for number in range(15)])
    body = [line for line in text.splitlines() if line.startswith("0-14")]
    assert len(body) == 1
    assert "A" in body[0]


def test_a_hole_is_written_as_a_gap_and_read_back_as_one():
    """Nothing may be interpolated across a capture hole."""
    text = a_document([(0, 0x8000, 0, 0), (5, 0x8000, 0, 0)])
    assert "gap" in text
    assert [number for number, _f in decode(text).frames] == [0, 5]


def test_the_buttons_read_as_names_not_numbers():
    text = a_document([(0, 0xE000, 0, 0)])
    assert "A+B+Z" in text


def test_an_authored_direction_expands_to_a_canonical_stick():
    """A person cannot meaningfully author `+58,+61`, so the format takes a
    named direction and a magnitude band and expands it. Both spellings parse
    to the same thing, and no reader has to know which it is holding."""
    text = "\n".join(["# sm64-inputs v1", "target: star WF 1", "version: us",
                      "fps: 30", "origin: authored", "--", "0-4  A  UR/full"])
    numbers = [number for number, _f in decode(text).frames]
    frame = decode(text).frames[0][1]
    assert numbers == [0, 1, 2, 3, 4]
    assert frame.buttons == 0x8000
    assert frame.stick_x > 0 and frame.stick_y > 0
    assert round((frame.stick_x ** 2 + frame.stick_y ** 2) ** 0.5) == 64


def test_neutral_parses_as_a_centred_stick():
    text = "\n".join(["# sm64-inputs v1", "target: star WF 1", "version: us",
                      "fps: 30", "origin: authored", "--", "0  -  neutral"])
    assert decode(text).frames == [(0, InputFrame(0, 0, 0, 0))]


def test_a_document_from_another_frame_rate_is_refused_with_a_reason():
    """Rescaling would move every input, so it is a load error naming why."""
    text = a_document([(0, 0, 0, 0)]).replace("fps:      30", "fps:      60")
    with pytest.raises(DocumentError, match="fps"):
        decode(text)


def test_a_document_with_an_unknown_button_is_refused():
    text = "\n".join(["# sm64-inputs v1", "target: star WF 1", "version: us",
                      "fps: 30", "origin: authored", "--", "0  X  neutral"])
    with pytest.raises(DocumentError, match="X"):
        decode(text)


def test_a_document_with_no_header_is_refused():
    with pytest.raises(DocumentError, match="header"):
        decode("0-4  A  UR/full")


def test_a_document_missing_a_required_header_field_is_refused():
    text = "\n".join(["# sm64-inputs v1", "version: us", "fps: 30",
                      "origin: authored", "--", "0  -  neutral"])
    with pytest.raises(DocumentError, match="target"):
        decode(text)


def test_an_unreadable_row_is_refused_rather_than_skipped():
    text = "\n".join(["# sm64-inputs v1", "target: star WF 1", "version: us",
                      "fps: 30", "origin: authored", "--", "nonsense"])
    with pytest.raises(DocumentError, match="row"):
        decode(text)


def test_a_comment_row_and_a_blank_row_are_ignored():
    text = "\n".join(["# sm64-inputs v1", "target: star WF 1", "version: us",
                      "fps: 30", "origin: authored", "--",
                      "# the setup", "", "0  A  neutral"])
    assert len(decode(text).frames) == 1


def test_an_empty_track_still_makes_a_loadable_document():
    got = decode(a_document([]))
    assert got.frames == []
    assert got.target == "star WF 1"


def test_a_centred_stick_writes_as_neutral():
    assert "neutral" in a_document([(0, 0x8000, 0, 0)])


def test_a_stick_inside_the_dead_zone_keeps_its_exact_value():
    """`neutral` is for EXACTLY centre. Rounding a real reading away would
    break the round trip on values the game itself still stores."""
    text = a_document([(0, 0, 3, -2)])
    assert "neutral" not in text
    assert decode(text).frames[0][1].stick_x == 3


def test_a_track_across_a_counter_restart_writes_rows_on_the_capture_axis():
    """The counter restarts on a console reset. Before the run derivation was
    shared, the document zero-based on the first frame alone and wrote a gap
    row that ran BACKWARDS (`2--889 - gap`) -- the timeline had already been
    fixed for the same input, in its own copy of the loop."""
    text = a_document([(900, 0x8000, 0, 0), (901, 0x8000, 0, 0),
                       (12, 0, 0, 0), (13, 0, 0, 0)])
    assert "gap" not in text
    assert [number for number, _ in decode(text).frames] == [0, 1, 2, 3]


# --- format v2: Mario rides in the document (2026-08-22) --------------------
# His ruling: "someone records a PERFECT INPUT EXAMPLE, with every piece of
# information about Mario that we need, as well as their controller data...
# I need to be able to compare my gameplay against the exact example,
# including all mario data".

def mario_rows(*specs):
    """spec: (number, action, yaw, speed) under a held A and a fixed stick."""
    return [(number, InputFrame(0x8000, 0, 10, 10, action, yaw, speed))
            for number, action, yaw, speed in specs]


def a_v2_document(rows):
    return encode(rows, target="star WF 1", strategy=None, version="us",
                  origin="test")


def test_marios_action_yaw_and_speed_round_trip():
    rows = mario_rows((0, A.ACT_DIVE, -1234, 31.25), (1, A.ACT_DIVE, -1234, 31.25))
    got = decode(a_v2_document(rows)).frames
    assert [(number, frame.action, frame.yaw, frame.speed)
            for number, frame in got] == [(0, A.ACT_DIVE, -1234, 31.25),
                                          (1, A.ACT_DIVE, -1234, 31.25)]


def test_a_known_action_writes_as_its_decomp_word_not_a_number():
    text = a_v2_document(mario_rows((0, A.ACT_DIVE, 0, 0.0)))
    assert " dive " in text
    assert "0x" not in text


def test_an_action_this_project_has_no_word_for_writes_as_its_hex_id():
    """A group name is not reversible, so an unknown action keeps its id."""
    text = a_v2_document(mario_rows((0, 0x0300088C, 0, 0.0)))
    assert "0x0300088C" in text
    assert decode(text).frames[0][1].action == 0x0300088C


def test_a_captured_speed_survives_as_the_same_float32():
    """RAM holds float32; the document writes the SHORTEST decimal that reads
    back as exactly that value, and the reader snaps through float32 so the
    two compare equal rather than differing in digits no frame ever held."""
    import struct
    awkward = struct.unpack("<f", struct.pack("<f", 28.123456))[0]
    text = a_v2_document(mario_rows((0, A.ACT_DIVE, 0, awkward)))
    assert "28.123455" in text                 # not 28.12345504760742
    assert decode(text).frames[0][1].speed == awkward


def test_a_facing_turning_under_a_held_stick_is_its_own_row():
    """Mario is written, so a yaw change is a real difference between rows."""
    text = a_v2_document(mario_rows((0, 0, 100, 0.0), (1, 0, 200, 0.0)))
    body = text.split("--\n", 1)[1].strip().splitlines()
    assert len(body) == 2


def test_a_v1_document_still_loads_with_mario_not_captured():
    text = ("# sm64-inputs v1\ntarget: star WF 1\nversion: us\nfps: 30\n"
            "origin: old\n--\n0-3  A  +10,+10\n")
    frame = decode(text).frames[0][1]
    assert (frame.buttons, frame.action, frame.yaw, frame.speed) == (0x8000, 0, 0, 0.0)


def test_a_hand_authored_row_may_stop_after_the_pad():
    text = a_v2_document([]) + "0-3       A        UR/full\n"
    frames = decode(text).frames
    assert len(frames) == 4 and frames[0][1].action == 0


def test_an_unknown_action_word_is_refused_with_the_row():
    text = a_v2_document([]) + "0  A  neutral  moonwalk  0  0\n"
    with pytest.raises(DocumentError, match="moonwalk"):
        decode(text)


def test_a_yaw_outside_the_games_s16_is_refused():
    text = a_v2_document([]) + "0  A  neutral  dive  70000  0\n"
    with pytest.raises(DocumentError, match="s16"):
        decode(text)


def test_a_row_with_the_wrong_number_of_words_is_refused():
    text = a_v2_document([]) + "0  A  neutral  dive  0\n"
    with pytest.raises(DocumentError, match="cannot read the row"):
        decode(text)


def test_author_credit_is_optional_and_round_trips():
    assert decode(a_document([])).author is None
    assert decode(a_document([], author="griffman1212")).author == "griffman1212"


@pytest.mark.parametrize("body", [
    "4-2 A neutral", "0-4 A neutral\n4-6 B neutral",
    "5 A neutral\n0 B neutral", "0-4 - gap\n4 A neutral",
    "0 A neutral\n0-4 - gap", "54000 A neutral",
    "0-999999999999 A neutral", "0 A +128,0", "0 A 0,-129",
    "0 A neutral dive 0 nan", "0 A neutral dive 0 inf",
    "0 A neutral dive 0 1e100",
])
def test_invalid_frame_axes_and_states_are_refused_before_expanding(body):
    with pytest.raises(DocumentError):
        decode(a_v2_document([]) + body + "\n")


def test_declared_gaps_keep_the_documents_full_frame_axis():
    got = decode(a_v2_document([]) + "0-4 - gap\n5 A neutral\n6-9 - gap\n")
    assert [number for number, _ in got.frames] == [5]
    assert got.frame_count == 10


def test_document_size_is_bounded_in_utf8_bytes():
    from sm64_events.inputs.document import MAX_DOCUMENT_BYTES
    with pytest.raises(DocumentError, match="large"):
        decode(a_v2_document([]) + "#" + "é" * (MAX_DOCUMENT_BYTES // 2))


def test_last_allowed_frame_and_raw_stick_edges_remain_exact():
    got = decode(a_v2_document([]) + "53999 A -128,+127 dive -32768 -12.5\n")
    assert got.frame_count == 54000
    number, frame = got.frames[0]
    assert (number, frame.stick_x, frame.stick_y, frame.speed) == (53999, -128, 127, -12.5)


def test_every_button_combination_survives_export_with_extreme_stick_values():
    # Pyramid's Cdown+Cleft filled the padded column and swallowed the stick.
    bits = [bit for bit, _ in A.BUTTON_BITS]
    rows = [(mask, InputFrame(sum(bit for i, bit in enumerate(bits) if mask & (1 << i)),
                              0, 0, 84, A.ACT_SPAWN_NO_SPIN_AIRBORNE, -32768, 0.0))
            for mask in range(1 << len(bits))]
    assert decode(a_v2_document(rows)).frames == rows


def test_named_document_preserves_unicode_name_and_legacy_is_unnamed():
    name = "Pyramid · Pillarless — clean setup"
    text = encode(frames([(0, 0, 0, 0)]), target="star 8 2", strategy=None,
                  version="us", origin="test", name=name)
    assert decode(text).name == name
    assert decode(a_document([(0, 0, 0, 0)])).name is None
