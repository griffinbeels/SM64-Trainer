import pytest

from sm64_events.inputs.document import DocumentError, encode
from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.templates import TemplateStore
from sm64_events.storage.db import Database


def a_document(spec=((0, 0x8000, 40, 40),), target="star 24 1",
               strategy="10 coin", origin="attempt 1") -> str:
    frames = [(number, InputFrame(buttons, 0, stick_x, stick_y))
              for number, buttons, stick_x, stick_y in spec]
    return encode(frames, target=target, strategy=strategy, version="us",
                  origin=origin)


@pytest.fixture
def templates(tmp_path):
    db = Database(tmp_path / "t.db")
    return TemplateStore(db._conn, db._lock)


def save(templates, **overrides):
    fields = dict(kind="star", entity_key="24-1", strat_tag="10 coin",
                  name="the good one", origin="attempt:1",
                  document=a_document())
    fields.update(overrides)
    return templates.save(**fields)


def test_a_saved_template_comes_back_with_its_frames(templates):
    template = save(templates)
    assert template.active is True
    assert template.frames() == [(0, InputFrame(0x8000, 0, 40, 40))]


def test_marking_a_new_template_stands_the_old_one_down(templates):
    first = save(templates, name="first")
    second = save(templates, name="second")
    assert templates.get(first.id).active is False
    assert templates.active_for("star", "24-1", "10 coin").id == second.id


def test_a_second_strategy_keeps_its_own_active_template(templates):
    ten_coin = save(templates, strat_tag="10 coin")
    fast = save(templates, strat_tag="fast")
    assert templates.active_for("star", "24-1", "10 coin").id == ten_coin.id
    assert templates.active_for("star", "24-1", "fast").id == fast.id


def test_a_strategy_with_no_template_of_its_own_falls_back(templates):
    """A template recorded before he named a strategy still describes the same
    movement; refusing to show it would hide real history behind a label."""
    shared = save(templates, strat_tag=None)
    assert templates.active_for("star", "24-1", "some new strat").id == shared.id


def test_no_template_anywhere_answers_none(templates):
    assert templates.active_for("star", "9-3", None) is None


def test_a_segment_gets_a_template_the_same_way_a_star_does(templates):
    """Star-segment parity: both are practiced things, both compare."""
    template = save(templates, kind="segment", entity_key="12")
    assert templates.active_for("segment", "12", "10 coin").id == template.id


def test_a_document_that_will_not_load_is_refused_at_SAVE_time(templates):
    """Validating on the way in, not on the way out: a template that cannot be
    decoded is useless, and finding that out when he opens a drawer is finding
    out at the worst possible moment."""
    with pytest.raises(DocumentError):
        save(templates, document="not a document at all")


def test_activating_an_older_template_stands_the_newer_one_down(templates):
    first = save(templates, name="first")
    save(templates, name="second")
    templates.activate(first.id)
    assert templates.active_for("star", "24-1", "10 coin").id == first.id


def test_deleting_a_template_erases_it(templates):
    """His ruling on deletion, 2026-08-02: marking a row removed is worthless.
    'Just completely erase them, it's cool.'"""
    template = save(templates)
    templates.delete(template.id)
    with pytest.raises(LookupError):
        templates.get(template.id)
    assert templates.active_for("star", "24-1", "10 coin") is None


def test_listing_names_every_template_for_an_entity(templates):
    save(templates, name="first")
    save(templates, name="second")
    save(templates, entity_key="9-3", name="elsewhere")
    names = [row.name for row in templates.list_for("star", "24-1")]
    assert sorted(names) == ["first", "second"]


@pytest.mark.parametrize("fields", [
    {"kind": "other"}, {"entity_key": ""}, {"entity_key": "24"},
    {"kind": "segment", "entity_key": "abc"}, {"name": "  "},
    {"name": "x" * 201}, {"document": a_document(spec=())},
])
def test_invalid_templates_do_not_replace_the_active_one(templates, fields):
    original = save(templates)
    with pytest.raises(ValueError):
        save(templates, **fields)
    assert templates.active_for("star", "24-1", "10 coin").id == original.id


def test_unknown_headers_and_author_survive_storage_exactly(templates):
    text = a_document().replace("--", "author: another player\nvideo: https://example.test/watch\n--")
    assert save(templates, document=text).document == text


def test_export_adds_library_name_without_rewriting_source_or_body(templates):
    from sm64_events.inputs.document import decode
    original = a_document().replace("--", "future: retained\n--", 1)
    template = save(templates, name="My clean setup", document=original)
    exported = template.export_document()
    assert decode(exported).name == "My clean setup"
    assert exported.split("--\n", 1)[1] == original.split("--\n", 1)[1]
    assert "future: retained" in exported
    assert templates.get(template.id).document == original


def test_export_preserves_valid_padded_separator_and_crlf_body(templates):
    from sm64_events.inputs.document import decode
    original = a_document().replace("\n", "\r\n").replace("--\r\n", "--   \r\n")
    template = save(templates, document=original)
    exported = template.export_document()
    assert exported.endswith(original[original.index("--   \r\n"):])
    assert decode(exported).frames == decode(original).frames
