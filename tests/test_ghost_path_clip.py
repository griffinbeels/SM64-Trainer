"""Which playing ghost the ghost path follows (`path_clip` in the native command).

The path is the fastest playing ghost's own trajectory (path-spec I2), so the
native session names that ghost in every command it sends: an index into the
command's own `clips`, never into the ticked list, because a ticked ghost the
renderer cannot play is not among the clips.
"""
from types import SimpleNamespace

from sm64_events.ghost import settings
from sm64_events.ghost.native_session import NativeGhost, fastest_clip

TARGET = {"kind": "star", "entity_key": "8-1", "strat": None}


class Capture:
    def __init__(self):
        self.commands = []

    def set_playback(self, *, blocking=True, **command):
        self.commands.append(command)
        return True

    def snapshot_status(self, *, blocking=True):
        return {"available": True, "state": 4, "playback_reason": 2, "displayed": True,
                "producer": SimpleNamespace(configuration=2, epoch=3)}


def ghost(attempt_id, frames, *, pb=False):
    return SimpleNamespace(id=attempt_id, origin=f"attempt:{attempt_id}", frames=frames, pb=pb)


def command_for(tmp_path, ghosts, *, playable=None):
    """The command the native session sends for `ghosts` (each attempt plays
    unless it is left out of `playable`)."""
    capture = Capture()
    playable = {g.id for g in ghosts} if playable is None else playable

    def find_native(attempt_id):
        if attempt_id not in playable:
            return None
        return (tmp_path / f"{attempt_id}.ghost",
                {"clip_id": f"{attempt_id:032x}", "sha256": "cd" * 32, "blink_frames": 0})

    replay = SimpleNamespace(recorder=SimpleNamespace(ghost_capture=capture),
                             native_identity=lambda attempt_id: {"attempt": attempt_id},
                             find_native=find_native)
    native = NativeGhost(replay, origin_of=lambda *_: {
        "kind": "star", "session_id": 1, "arm_id": 9, "frame": 100, "utc": "2026-10-03T08:00:00+00:00"})
    native.resolve(TARGET, ghosts)
    native.reconcile(enabled=True, target=TARGET, controls=settings.native_controls(settings.defaults()))
    native.note()
    command = capture.commands[-1]
    assert command["enabled"], command
    return command


def test_the_path_follows_the_fastest_playing_ghost(tmp_path):
    command = command_for(tmp_path, [ghost(11, 900, pb=True), ghost(12, 850), ghost(13, 870)])
    assert [entry["clip_id"] for entry in command["clips"]] == [bytes.fromhex(f"{n:032x}") for n in (11, 12, 13)]
    assert command["path_clip"] == 1


def test_the_index_is_into_the_clips_not_the_ticked_ghosts(tmp_path):
    """The fastest ticked ghost cannot play here, so it is not a clip; the
    path follows the fastest of those that are."""
    command = command_for(tmp_path, [ghost(11, 900, pb=True), ghost(12, 800), ghost(13, 850)],
                          playable={11, 13})
    assert len(command["clips"]) == 2 and command["path_clip"] == 1


def test_one_ghost_is_its_own_path(tmp_path):
    assert command_for(tmp_path, [ghost(11, 900, pb=True)])["path_clip"] == 0


def test_a_tie_keeps_the_first_and_an_unknown_time_is_never_the_fastest():
    assets = lambda *times: [("path", {}, "label", frames) for frames in times]  # noqa: E731
    assert fastest_clip(assets(850, 850)) == 0
    assert fastest_clip(assets(None, 900)) == 1
    assert fastest_clip(assets(None, None)) == 0
    assert fastest_clip(assets(900, True, 950)) == 0  # a flag is not a run time
    assert fastest_clip([]) == 0
