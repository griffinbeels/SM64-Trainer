"""Native display labels retain portable credit and use the visible rival slot."""
from types import SimpleNamespace

import pytest

from sm64_events.ghost.native_layout import GHOSTS, LABEL_BYTES
from sm64_events.ghost.native_protocol import label_bytes
from sm64_events.ghost.native_session import NativeGhost, ghost_label
from sm64_events.ghost.portable import make_entry, write_package
from tests.test_ghost_native_session import Capture
from tests.test_ghost_sharing import provider, recording


def imported(author, **fields):
    return SimpleNamespace(author=author, import_id="imported", **fields)


@pytest.mark.parametrize("author", [
    "SullyLikesBigChungus", "WaterproofTeabag_", "Runner One", "Run&Jump:1",
    "' .^|<>,_-()+&:!%?~$", "W" * 20, "+" * 20,
])
def test_supported_credit_remains_readable_and_wire_valid(author):
    ghost = imported(author)
    assert ghost_label(ghost) == author
    assert label_bytes(ghost_label(ghost)).split(b"\0", 1)[0] == author.encode("ascii")
    assert ghost.author == author


@pytest.mark.parametrize("author", ["ガミル", "Zoë", "Runner🎮", "slash/name", "a\u200bb", "", None])
def test_unsupported_credit_uses_slot_number_without_guessing_identity(author):
    ghost = imported(author, rival_slot=3)
    assert ghost_label(ghost, 1) == "GHOST 4"
    assert ghost.author == author


@pytest.mark.parametrize("slot", [None, True, -1, GHOSTS, "3"])
def test_unassigned_or_invalid_slot_uses_the_rosters_non_pb_ordinal(slot):
    assert ghost_label(imported("ガミル", rival_slot=slot), 2) == "GHOST 2"


@pytest.mark.parametrize("author", ["W" * 63, "W" * 64, "W" * 128])
def test_legacy_credit_is_only_shortened_on_the_native_wire(author):
    ghost = imported(author)
    label = ghost_label(ghost)
    assert ghost.author == author
    assert len(label_bytes(label)) == LABEL_BYTES - 1
    assert label == (author if len(author) < LABEL_BYTES else "W" * 60 + "...")


def test_personal_labels_take_precedence_over_import_credit_and_slot():
    assert ghost_label(imported("ガミル", pb=True, rival_slot=3)) == "YOUR PB"
    assert ghost_label(SimpleNamespace(pb=True), 0) == "YOUR PB"
    assert ghost_label(SimpleNamespace(pb=False, author="ガミル")) == "YOUR RUN"


def test_real_imports_keep_exact_credit_while_prepared_command_uses_readable_labels(tmp_path):
    sharing = provider(tmp_path)
    entries = []
    for index, author in enumerate(("ガミル", "WaterproofTeabag_")):
        folder = tmp_path / str(index)
        folder.mkdir()
        path, entry = recording(folder, pad=(index + 1, 0, 0))
        entry = make_entry(path, author=author, target=entry["target"], frames=entry["frames"],
                           clock=entry["clock"], recorded_utc=entry["recorded_utc"],
                           practice=False, outcome="success")
        entries.append((entry, path))
    package = tmp_path / "credits.sm64ghosts"
    write_package(package, entries)
    assert sharing.import_file(package)["imported"] == 2
    originals = {ghost.author: ghost for ghost in sharing.imports_for("star", "2-1")}
    # The UI owns these slots; roster order, PB insertion and hue do not renumber them.
    class InSlot:
        def __init__(self, ghost, slot):
            self.ghost, self.rival_slot = ghost, slot

        def __getattr__(self, name):
            return getattr(self.ghost, name)

    source = Capture()
    replay = SimpleNamespace(recorder=SimpleNamespace(ghost_capture=source),
                             native_identity=lambda _: {}, find_native=lambda _: None)
    native = NativeGhost(replay, portable=sharing)
    pb = SimpleNamespace(id=7, pb=True, origin="attempt:7", author=None)
    ghosts = [pb, InSlot(originals["ガミル"], 3), InSlot(originals["WaterproofTeabag_"], 0)]
    answer = native.prepare(
        {"kind": "star", "entity_key": "2-1", "strat": "10 coin"}, ghosts,
        {"hue_degrees": 48}, 1)
    assert [(row["author"], row["label"]) for row in answer["ghosts"]] == [
        (None, "YOUR PB"), ("ガミル", "GHOST 4"), ("WaterproofTeabag_", "WaterproofTeabag_")]
    assert [clip["label"] for clip in source.commands[-1]["clips"]] == ["GHOST 4", "WaterproofTeabag_"]
    assert {ghost.author for ghost in sharing.imports_for("star", "2-1")} == set(originals)
    # Legacy callers without a slot remain deterministic and exclude the PB.
    answer = native.resolve({"kind": "star", "entity_key": "2-1"}, [pb, originals["ガミル"]])
    assert answer["ghosts"][1]["label"] == "GHOST 1"
    native.stop()
