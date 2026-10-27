"""Stage ghosts (M4 round 4, lab only): which saved ghosts the scene viewer draws into its stage,
where each stands in it, and how they look.

    ghosts = StageGhosts()
    answer = ghosts.replace(host, [{"ref": ..., "label": ...}], load_ghost)   # {"shown", "refused"}
    block = ghosts.block(look, frame_offset)                                   # one picture's ghosts

The stage is the scene the user opened (its world, camera and sound). Every ghost added to it is
a saved recording drawn into that world by the renderer's in-game ghost drawing
(plugin/gfxwrap/ghost_viewer.h): its look, its name box, hidden behind walls. A ghost's clip
loads into the renderer once, when it is added (a slot of viewer_host.c's), and each picture then
names only the slots and each one's frame, so any order of pictures (play, steps, seeks, back)
draws every ghost exactly where its recording puts it.

Alignment: each ghost's first frame is the stage attempt's first frame. Scene frame k shows the
ghost row `ghost_frame(timer(k), timer(origin))` game frames into its recording: the tap's
global_timer counts game frames, so lag frames and held pictures stay in step with the stage.
Past its recording a star ghost holds its last pose and fades out as it does at the end of a race
in the game; any other ghost is gone (ghost_viewer.cpp choose).

Labels: each name box says what the game's ghost would (native_session.ghost_label: YOUR PB,
YOUR RUN, the credited player, GHOST n). One viewer-only difference, his round-4 goal ("understand
the differences between different ghosts clearly"): when two or more ghosts in the scene would
carry the same label, each of those adds its time (YOUR RUN 11''03); a label no other ghost shares
stays exactly the game's. The game's dialog font has no double quote, so the time's " is drawn as
two apostrophes, which look the same there. A long label gives up its end, never the time.

The look follows the player's ghost settings (ghost/settings.py, the in-game ghost's): opacity,
colour (each ghost's hue steps from his as in the game's set: native_session.ghost_hue), shadow,
name boxes and their box, and the inputs over the name. The live race's own effects stay off: the
fade where a ghost overlaps or hides live Mario (the stage's runner is not live Mario here), the
corner input stack and the path. Each ghost's own sound plays beside the stage's (ghost_sound.py).

A ghost's view (round 5, item 5, his words: "one button per active ghost in the scene to follow
their recorded POV"): the stage's world from the camera that ghost's own game drew with (its clip's
camera, Frame.camera), at the same row its drawing shows for the picture (ghost_frame's count, the
row ghost_viewer.cpp chooses). The stage's world is the only world, so a camera recorded in another
level or area than the stage's frame does not apply there. Where its own row has no camera that
applies (a gap in the recording, before its start, after its end, another area), the view is the
nearest one that does, the last before the row first, with a plain reason; with none at all, the
recorded camera and why. Only the screen roll and the field of view travel: the look-at roll the
game leaves at 0 in play is not one the free camera can set (camera_state.place).

`load_ghost(ref)` is the clip loader (interface C: {"clip", "player", "frames", "label"} and,
optionally, "time": the bare run time; or LookupError with a plain sentence). The catalog's (server/scene_ghosts.py) serves the page; until
it is wired, LocalGhosts reads the player's own attempts from the replay store's native folder.
"""
from __future__ import annotations

import bisect
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import viewer  # noqa: E402  (also puts src/ on sys.path through replay)
from sm64_events.core.timefmt import format_igt_short  # noqa: E402
from sm64_events.ghost import settings  # noqa: E402
from sm64_events.ghost.native_session import ghost_hue, ghost_label  # noqa: E402

MAX_GHOSTS = viewer.GHOST_SLOTS
TOO_MANY = f"A scene shows at most {MAX_GHOSTS} ghosts at once. Remove one to add another."
NO_SCENE_STATE = "This recording carries no scene state, so ghosts cannot be placed in it."
NO_RENDERER = "This renderer cannot draw ghosts (it is not the Trainer's renderer, or an older build of it)."
NOT_PLAYABLE = "This ghost's recording could not be played: the game's ghost refuses it."
REFUSALS = {-2: "The renderer could not build the ghost's model from the ROM.",
            -3: NOT_PLAYABLE,
            -4: "There is not enough memory to load this ghost.",
            -5: "The renderer refused the ghost capture layout."}
LOADER_FAILED = "This ghost could not be loaded."
# Why a ghost's view is not its own row's camera (its view): the cause, then which view it shows.
VIEW_BEFORE = "This ghost's run has not started yet"
VIEW_ENDED = "This ghost's recording has ended"
VIEW_GAP = "This ghost's recording has no camera for this moment"
VIEW_ELSEWHERE = "This ghost is in another area of the level here"
VIEW_LAST, VIEW_NEXT, VIEW_FIRST = (", so this is the last view it had.", ", so this is the next view it has.",
                                    ", so this is its first view.")
VIEW_NONE = "This ghost's recording has no camera in this area, so this shows the recorded camera."
VIEW_GONE = "That ghost is no longer in the scene, so this shows the recorded camera."
VIEW_NO_CLOCK = "This picture has no game clock to find the ghost's moment by, so it shows the recorded camera."
LABEL_FIT = 28                # characters a name box shows before it cuts the end (180 layout px)
TIME_CHARACTERS = frozenset("0123456789'\"")


def ghost_frame(timer: int, origin_timer: int) -> int:
    """The ghost's row for a scene frame: game frames since the stage's first frame (negative
    before it, where a ghost shows its first pose, as the game's ghost does before its start)."""
    return int(timer) - int(origin_timer)


def distinct_labels(ghosts: list[tuple[str, str | None]]) -> list[str]:
    """Each ghost's name box text from its (in-game label, bare time): a label two or more ghosts
    share takes its ghost's time; the rest stay exactly the game's."""
    counts: dict[str, int] = {}
    for base, _time in ghosts:
        counts[base] = counts.get(base, 0) + 1
    return [with_time(base, time) if counts[base] > 1 and time else base for base, time in ghosts]


def with_time(label: str, time: str) -> str:
    """`label` and its time, the time's " drawn as two apostrophes (the dialog font has no double
    quote); a label too long for both gives up its end, never the time."""
    text = time.replace('"', "''")
    if len(label) + 1 + len(text) > LABEL_FIT:
        label = label[:max(1, LABEL_FIT - len(text) - 4)] + "..."
    return f"{label} {text}"


def bare_time(value) -> str | None:
    """A bare time as the catalog lists it (11"03, 1'02"16), or None for anything else."""
    return value if isinstance(value, str) and 0 < len(value) <= 12 and set(value) <= TIME_CHARACTERS else None


def label_text(label, number: int) -> str:
    """A name box's text by the in-game rule (native_session.ghost_label): the label as given
    when the cartridge's font can write it and it fits, else "GHOST n"."""
    return ghost_label(SimpleNamespace(pb=False, import_id="scene", rival_slot=None,
                                       author=label if isinstance(label, str) else None), number)


@dataclass(frozen=True)
class Look:
    """What the ghost settings ask of a viewer ghost (settings.native_controls' fields)."""
    opacity: float = 1.0
    tint: bool = True
    hue_degrees: int = 250
    shadow: bool = True
    names: bool = True
    white_name: bool = False
    name_background_opacity: float = 0.6
    inputs: bool = False
    values: bool = False
    volume: float = settings.DEFAULT_VOLUME    # its sound's level (ghost_sound.py), as in the game

    @classmethod
    def of(cls, values: dict | None = None) -> Look:
        """From the ghost session's values (GhostSession.payload(), or settings.defaults())."""
        merged = settings.defaults()
        merged.update({name: value for name, value in (values or {}).items() if name in merged})
        native = settings.native_controls(merged)
        return cls(float(native["opacity"]), bool(native["tint_enabled"]), int(native["hue_degrees"]),
                   bool(native["shadow_enabled"]), bool(native["names_enabled"]),
                   native["name_background"] == settings.NAME_BACKGROUNDS["white"],
                   float(native["name_background_opacity"]), bool(native["inputs_enabled"]),
                   native["input_mode"] == settings.INPUT_MODES["values"], float(native["volume"]))

    def packed(self) -> tuple[float, float, int]:
        flags = viewer.GHOST_FLAGS
        chosen = (("tint", self.tint), ("shadow", self.shadow), ("names", self.names), ("inputs", self.inputs),
                  ("values", self.values), ("white_name", self.white_name))
        return self.opacity, self.name_background_opacity, sum(flags[name] for name, on in chosen if on)


@dataclass
class Shown:
    ref: str
    slot: int
    label: str                   # what its name box shows now (distinct_labels)
    player: str | None
    frames: int | None
    base: str = ""               # the game's label for it (label_text)
    time: str | None = None      # its bare run time, when the loader or the page knows it
    clip: bytes = b""            # its recording, kept to load again under a new label
    views: GhostCameras | None = None   # its cameras, read from the clip the first time its view is asked


class GhostCameras:
    """One ghost's recorded cameras, row by row as its playback indexes them (Clip.indexed_rows):
    each row's coordinate, level and area, and its camera as the free camera takes it
    ({"focus", "position", "roll": the screen roll, "fov"}; None where the row has none)."""

    def __init__(self, clip: bytes):
        from sm64_events.ghost import clip as ghost_clip
        try:
            rows = ghost_clip.decode_clip(clip).indexed_rows()
        except (ValueError, LookupError):
            rows = []
        self.coordinates = [coordinate for coordinate, _frame in rows]
        self.places = [(frame.level, frame.area) for _coordinate, frame in rows]
        self.cameras = [self._free(frame.camera) for _coordinate, frame in rows]

    @staticmethod
    def _free(camera: dict | None) -> dict | None:
        if camera is None:
            return None
        return {"focus": camera["focus"], "position": camera["position"], "roll": camera["screen_roll"],
                "fov": camera["fov"]}

    def at(self, frame: int, place: tuple | None) -> tuple[dict | None, str | None]:
        """(camera, reason) for the scene picture `frame` game frames after the ghost's first
        (ghost_frame) in the stage's `place` ((level, area); None: unknown, any applies). Its own
        row is the one its drawing shows (ghost_viewer.cpp choose: the first before its start, the
        one whose coordinate is the first's plus `frame`, none past its last); where that row has
        no camera in this place, the nearest earlier row's that has, else the nearest later's, with
        the reason; (None, VIEW_NONE) when no row has one."""
        count = len(self.coordinates)
        if not count:
            return None, VIEW_NONE

        def usable(row: int) -> bool:
            return self.cameras[row] is not None and (place is None or self.places[row] == place)
        target = self.coordinates[0] + max(0, frame)
        if frame < 0:
            row, own, cause = 0, None, VIEW_BEFORE
        elif target > self.coordinates[-1]:
            row, own, cause = count, None, VIEW_ENDED
        else:
            row = bisect.bisect_left(self.coordinates, target)
            own = row if row < count and self.coordinates[row] == target else None
            if own is not None and usable(own):
                return self.cameras[own], None
            cause = VIEW_ELSEWHERE if own is not None and self.cameras[own] is not None else VIEW_GAP
        before = next((at for at in range(row - 1, -1, -1) if usable(at)), None)
        if before is not None:
            return self.cameras[before], cause + VIEW_LAST
        after = next((at for at in range(row if own is None else own + 1, count) if usable(at)), None)
        if after is not None:
            return self.cameras[after], cause + (VIEW_FIRST if frame < 0 else VIEW_NEXT)
        return None, VIEW_NONE


class StageGhosts:
    """The scene's ghosts in the page's order, each in a renderer slot of its own."""

    def __init__(self):
        self.shown: list[Shown] = []

    def __bool__(self) -> bool:
        return bool(self.shown)

    def replace(self, host, entries, load_ghost) -> dict:
        """Makes the set `entries` ([{"ref", "label"}], the page's whole set, in order): a ghost
        already shown keeps its slot, a new one loads (load_ghost, then the renderer's own check of
        its recording), one no longer named leaves its slot empty. Returns {"shown": refs,
        "refused": [{"ref", "reason"}]}; ValueError for a malformed set."""
        wanted = checked(entries)
        kept = {ghost.ref: ghost for ghost in self.shown}
        shown, refused = [], []
        for entry in wanted:
            ghost = kept.pop(entry["ref"], None)
            if ghost is not None:
                shown.append(ghost)
                continue
            if len(shown) >= MAX_GHOSTS:
                refused.append({"ref": entry["ref"], "reason": TOO_MANY})
                continue
            loaded = self._load(host, entry, load_ghost, used={g.slot for g in shown} | {g.slot for g in kept.values()},
                                number=len(shown) + 1)
            if isinstance(loaded, str):
                refused.append({"ref": entry["ref"], "reason": loaded})
            else:
                shown.append(loaded)
        for gone in kept.values():
            host.ghost_load(gone.slot, b"", "")
        self._relabel(host, shown)
        self.shown = shown
        return {"shown": [ghost.ref for ghost in shown], "refused": refused}

    @staticmethod
    def _relabel(host, shown: list[Shown]) -> None:
        """Gives every ghost its distinct label (distinct_labels), loading again each one whose
        label changed: a second YOUR RUN adds both times, its removal takes them away."""
        for ghost, label in zip(shown, distinct_labels([(g.base, g.time) for g in shown]), strict=True):
            if label != ghost.label and not host.ghost_load(ghost.slot, ghost.clip, label):
                ghost.label = label

    def view(self, ref: str, frame: int, place: tuple | None) -> tuple[dict | None, str | None]:
        """Ghost `ref`'s view at `frame` game frames since the stage's first (GhostCameras.at), its
        cameras read from its clip the first time; (None, VIEW_GONE) for a ghost not in the set."""
        ghost = next((ghost for ghost in self.shown if ghost.ref == ref), None)
        if ghost is None:
            return None, VIEW_GONE
        if ghost.views is None:
            ghost.views = GhostCameras(ghost.clip)
        return ghost.views.at(frame, place)

    def clear(self, host) -> None:
        for ghost in self.shown:
            host.ghost_load(ghost.slot, b"", "")
        self.shown = []

    @staticmethod
    def _load(host, entry: dict, load_ghost, used: set, number: int) -> Shown | str:
        try:
            got = load_ghost(entry["ref"])
        except LookupError as error:
            return str(error) or LOADER_FAILED
        clip = got.get("clip") if isinstance(got, dict) else None
        if not isinstance(clip, (bytes, bytearray)) or not clip:
            return LOADER_FAILED
        label = label_text(got.get("label") or entry.get("label"), number)
        slot = min(set(range(MAX_GHOSTS)) - used)
        result = host.ghost_load(slot, bytes(clip), label)
        if result:
            host.ghost_load(slot, b"", "")
            return REFUSALS.get(result, NOT_PLAYABLE)
        return Shown(entry["ref"], slot, label, got.get("player"), got.get("frames"), label,
                     bare_time(got.get("time")) or bare_time(entry.get("time")), bytes(clip))

    def block(self, look: Look, frame: int) -> bytes | None:
        """One picture's ghosts block (viewer.ghost_block): every shown ghost at `frame` game frames
        since the stage's first, its hue the game's for its place in the set; None without ghosts."""
        if not self.shown:
            return None
        return viewer.ghost_block(look.packed(), [(ghost.slot, frame, ghost_hue(look.hue_degrees, index))
                                                  for index, ghost in enumerate(self.shown)])


def checked(entries) -> list[dict]:
    if not isinstance(entries, list):
        raise ValueError("ghosts' set is a list of {ref, label}")
    seen, wanted = set(), []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("ref"), str) or not entry["ref"] \
                or len(entry["ref"]) > 512 or not isinstance(entry.get("label", ""), (str, type(None))) \
                or not isinstance(entry.get("time"), (str, type(None))):
            raise ValueError("each ghost is {ref, label[, time]}: a ref the catalog listed, its label and time")
        if entry["ref"] not in seen:
            seen.add(entry["ref"])
            wanted.append(entry)
    return wanted


class LocalGhosts:
    """A stand-in clip loader (interface C's shape) until the catalog's is wired: refs
    "attempt:<id>" name the player's own saved runs in a replay store's native folder
    (replay/service.py find_native's <save_root>/native/attempt_<id>.ghost), labeled as the game
    labels his runs ("YOUR RUN"). Tests and the lab's default use it; it lists nothing itself."""

    def __init__(self, native_dir: Path):
        self.native_dir = Path(native_dir)

    def __call__(self, ref: str) -> dict:
        kind, _, number = str(ref).partition(":")
        if kind != "attempt" or not number.isdigit():
            raise LookupError("This ghost is not one of yours on this computer.")
        path = self.native_dir / f"attempt_{int(number)}.ghost"
        try:
            data = path.read_bytes()
        except OSError:
            raise LookupError("This run has no saved Mario poses on this computer.") from None
        from sm64_events.ghost import clip as ghost_clip
        try:
            frames = len(ghost_clip.decode_clip(data).frames)
        except (ValueError, LookupError):
            raise LookupError(NOT_PLAYABLE) from None
        return {"clip": data, "player": "You", "frames": frames, "time": self._time(path),
                "label": ghost_label(SimpleNamespace(pb=False, import_id=None))}

    @staticmethod
    def _time(path: Path) -> str | None:
        """The run's time from its manifest: the frames from its clock's start to its closing frame."""
        try:
            manifest = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
            run = manifest["closed_frame"] - manifest["native_clock_frame"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return format_igt_short(run) if type(run) is int and run > 0 else None
