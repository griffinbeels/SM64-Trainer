import pytest

from sm64_events.inputs.document import DocumentError, decode, encode
from sm64_events.inputs.frame import InputFrame


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


def test_a_facing_turning_under_a_held_stick_does_not_split_the_row():
    """The document writes only the pad, so two frames that differ only in
    what it does not write are one row."""
    rows = [(0, InputFrame(0x8000, 0, 10, 10, 0, 100)),
            (1, InputFrame(0x8000, 0, 10, 10, 0, 200))]
    body = encode(rows, target="star WF 1", strategy=None, version="us",
                  origin="test").split("--\n", 1)[1]
    assert body.strip().splitlines() == ["0-1       A        +10,+10"]
