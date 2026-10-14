"""Source credit stays paired with each selected clip; controller controls are remembered."""
import asyncio
from types import SimpleNamespace

import pytest

from sm64_events.ghost import settings
from sm64_events.ghost.native_protocol import LABEL_BYTES, label_bytes
from sm64_events.ghost.native_session import NativeGhost, ghost_label
from sm64_events.ghost.pb import AttemptGhost, GhostLibrary
from sm64_events.ghost.selection import GhostSelection, MAX_GHOSTS, checked_choice, chosen_ghosts
from test_ghost_native_session import Capture
from test_ghost_session import a_session, a_store

TARGET = {"kind": "star", "entity_key": "24-1", "strat": "10 coin"}
ORIGIN = {"kind": "star", "session_id": 1, "arm_id": 9, "frame": 100,
          "utc": "2026-09-21T08:00:00+00:00"}


def imported(identity="ab" * 32, author="Another player", **changes):
    return SimpleNamespace(**{**dict(id=identity, import_id=identity, attempt_id=None, origin=f"ghost:{identity}",
                                    kind="star", entity_key="24-1", strat_tag="10 coin", frames=300,
                                    author=author, pb=False, name=author), **changes})


class Collection:
    generation = 1

    def __init__(self, ghosts, assets=None):
        self.ghosts, self.assets, self.calls = ghosts, assets or {}, []

    def imports_for(self, kind, key):
        return [ghost for ghost in self.ghosts if (ghost.kind, ghost.entity_key) == (kind, key)]

    def get(self, identity):
        return next(ghost for ghost in self.ghosts if ghost.id == identity)

    def resolve(self, ghost, target):
        self.calls.append((ghost.id, target))
        if ghost.id not in self.assets:
            raise LookupError("The imported Mario recording is missing.")
        return self.assets[ghost.id]


def asset(tmp_path, key):
    """A playable clip. Its manifest states complete inputs, as every
    playable one must (his rule, round 40: a recording with any unknown
    input is not a ghost at any door; ghost/clip.py INCOMPLETE_INPUTS)."""
    path = tmp_path / f"{key}.ghost"
    path.write_bytes(b"validated recording")
    path.with_suffix(".json").write_text("{}", encoding="utf-8")
    return path, {"clip_id": key * 16, "sha256": "cd" * 32, "blink_frames": 0, "inputs_complete": True}


def test_labels_follow_source_and_current_pb_membership_after_cached_resolution(tmp_path):
    local = asset(tmp_path, "01")
    other, absent = imported(), imported("ef" * 32, "Missing player")
    collection = Collection([absent, other], {other.id: asset(tmp_path, "02")})
    capture, reads = Capture(), []
    replay = SimpleNamespace(recorder=SimpleNamespace(ghost_capture=capture),
                             native_identity=lambda _: "unchanged",
                             find_native=lambda number: reads.append(number) or local)
    native = NativeGhost(replay, origin_of=lambda *_: dict(ORIGIN), portable=collection)
    controls = settings.native_controls(settings.defaults())
    for pb, expected in ((True, "YOUR PB"), (False, "YOUR RUN"), (True, "YOUR PB")):
        ghosts = [absent, AttemptGhost(7, "star", "24-1", "10 coin", pb=pb), other]
        answer = native.resolve(TARGET, ghosts)
        native.reconcile(enabled=True, target=TARGET, controls=controls)
        native.note()
        clips = capture.commands[-1]["clips"]
        assert [clip["label"] for clip in clips] == [expected, other.author]
        assert [clip["clip_path"] for clip in clips] == [str(local[0]), str(collection.assets[other.id][0])]
        assert [row["available"] for row in answer["ghosts"]] == [False, True, True]
        assert answer["ghosts"][2]["import_id"] == other.id and answer["ghosts"][2]["attempt_id"] is None
    assert reads == [7]  # credits changed without rereading the immutable poses


def test_imported_resolution_never_borrows_a_foreign_local_attempt(tmp_path):
    other = imported()
    collection = Collection([other], {other.id: asset(tmp_path, "01")})
    native = NativeGhost(SimpleNamespace(), portable=collection)
    assert not native.probe({**TARGET, "entity_key": "24-2"}, other)["available"]
    assert collection.calls == []


def test_same_clip_selected_as_local_and_imported_plays_once_with_first_credit(tmp_path):
    other, local = imported(), asset(tmp_path, "01")
    collection = Collection([other], {other.id: local})
    capture = Capture()
    replay = SimpleNamespace(recorder=SimpleNamespace(ghost_capture=capture),
                             native_identity=lambda _: "unchanged", find_native=lambda _: local)
    native = NativeGhost(replay, origin_of=lambda *_: dict(ORIGIN), portable=collection)
    for ghosts, credit in (([AttemptGhost(7, "star", "24-1", "10 coin", pb=True), other], "YOUR PB"),
                           ([other, AttemptGhost(7, "star", "24-1", "10 coin")], other.author)):
        answer = native.resolve(TARGET, ghosts)
        native.reconcile(enabled=True, target=TARGET, controls=settings.native_controls(settings.defaults()))
        native.note()
        assert [clip["label"] for clip in capture.commands[-1]["clips"]] == [credit]
        assert answer["ghosts"][1]["reason"] == "duplicate_clip"
        assert answer["ghosts"][1]["reason_text"] == "Already shown by another selected ghost."


def test_label_bound_keeps_the_credit_intact_and_does_not_invent_one():
    """The native field is bounded; the credit itself never changes. Round 19
    (2026-10-08, 90921b04): the renderer draws only the ROM's own glyphs, so
    a credit it cannot draw, or no credit, shows its rival number instead of
    a guessed or invented name."""
    author = "  A\nB  " + "W" * 90
    other = imported(author=author)
    label = ghost_label(other)
    assert label.startswith("A B W") and label.endswith("...")
    assert len(label_bytes(label)) == LABEL_BYTES - 1
    assert label_bytes(label).split(b"\0", 1)[0].decode("ascii") == label
    assert other.author == author
    assert ghost_label(imported(author="  A\nB  " + "é" * 90)) == "RIVAL 1"
    assert ghost_label(imported(author="")) == "RIVAL 1"


@pytest.mark.parametrize("label", [None, 12, "a\0b", "a\nb", "x" * LABEL_BYTES, "\ud800"])
def test_channel_refuses_invalid_labels(label):
    with pytest.raises(ValueError):
        label_bytes(label)


@pytest.mark.parametrize("imports", [["wrong"], [True], ["ab" * 32] * 2])
def test_imported_choices_are_validated(imports):
    with pytest.raises(ValueError):
        checked_choice({"pb": False, "attempts": [], "imports": imports})


def test_imported_and_local_ghosts_share_the_limit_without_reinterpreting_ids():
    identities = [f"{number:064x}" for number in range(MAX_GHOSTS)]
    choice = checked_choice({"pb": False, "attempts": [], "imports": identities})
    assert choice["imports"] == identities
    with pytest.raises(ValueError, match="at most"):
        checked_choice({**choice, "attempts": [7]})
    assert checked_choice({"pb": True, "attempts": []}) == {"pb": True, "attempts": []}


def test_collection_choices_are_target_bound_and_remembered(tmp_path):
    other = imported()
    collection = Collection([other])
    library = GhostLibrary(a_store(tmp_path), None, portable=collection)
    selection = GhostSelection()
    choice = {"pb": False, "attempts": [], "imports": [other.id]}
    stored = selection.choose(TARGET, choice)
    database = SimpleNamespace(get_state=lambda *_: stored)
    restored = GhostSelection()
    restored.load(database)
    assert restored.chosen(TARGET, library) == choice
    assert chosen_ghosts(choice, library, TARGET) == ([other], [])
    assert chosen_ghosts(choice, library, {**TARGET, "entity_key": "24-2"}) == ([], [other.id])


def test_candidate_metadata_retains_practice_state_for_explicit_collection_choices(tmp_path):
    other = imported(practice=True)
    selection = GhostSelection()
    library = GhostLibrary(a_store(tmp_path), None, portable=Collection([other]))
    native = SimpleNamespace(probe=lambda *_: {"available": True, "reason_text": None})
    candidate = selection.candidates(TARGET, library, native)[0]
    assert candidate["practice"] is True and candidate["native_available"] is True
    other.practice = False
    candidate = selection.candidates(TARGET, library, native)[0]
    assert candidate["practice"] is False and candidate["native_available"] is True


def test_legacy_template_choice_remains_readable_before_the_library_arrives():
    from sm64_events.ghost.selection import target_key

    selection = GhostSelection()
    selection.load(SimpleNamespace(get_state=lambda *_: {target_key(TARGET): 7}))
    assert selection.chosen(TARGET) == {"pb": True, "attempts": []}


def test_session_admits_only_current_target_collection_choices_atomically(tmp_path):
    other = imported()
    session, *_ = a_session(tmp_path)
    session._portable = Collection([other])
    choice = {"pb": False, "attempts": [], "imports": [other.id]}
    result = asyncio.run(session.update(ghosts=choice, target=session._read_target()))
    assert result["selection"]["ghosts"] == choice
    before = session.payload()
    with pytest.raises(ValueError, match="not available for this star"):
        asyncio.run(session.update(ghosts={**choice, "imports": ["ef" * 32]},
                                   target=session._read_target(), opacity=.2))
    assert session.payload() == before
