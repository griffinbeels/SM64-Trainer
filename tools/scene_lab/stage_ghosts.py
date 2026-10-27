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

The look follows the player's ghost settings (ghost/settings.py, the in-game ghost's): opacity,
colour (each ghost's hue steps from his as in the game's set: native_session.ghost_hue), shadow,
name boxes and their box, and the inputs over the name. The live race's own effects stay off: the
fade where a ghost overlaps or hides live Mario (the stage's runner is not live Mario here), the
corner input stack, the path and the ghost's sound.

`load_ghost(ref)` is the clip loader (interface C: {"clip", "player", "frames", "label"}, or
LookupError with a plain sentence). The catalog's (server/scene_ghosts.py) serves the page; until
it is wired, LocalGhosts reads the player's own attempts from the replay store's native folder.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import viewer  # noqa: E402  (also puts src/ on sys.path through replay)
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


def ghost_frame(timer: int, origin_timer: int) -> int:
    """The ghost's row for a scene frame: game frames since the stage's first frame (negative
    before it, where a ghost shows its first pose, as the game's ghost does before its start)."""
    return int(timer) - int(origin_timer)


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
                   native["input_mode"] == settings.INPUT_MODES["values"])

    def packed(self) -> tuple[float, float, int]:
        flags = viewer.GHOST_FLAGS
        chosen = (("tint", self.tint), ("shadow", self.shadow), ("names", self.names), ("inputs", self.inputs),
                  ("values", self.values), ("white_name", self.white_name))
        return self.opacity, self.name_background_opacity, sum(flags[name] for name, on in chosen if on)


@dataclass
class Shown:
    ref: str
    slot: int
    label: str
    player: str | None
    frames: int | None


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
        self.shown = shown
        return {"shown": [ghost.ref for ghost in shown], "refused": refused}

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
        return Shown(entry["ref"], slot, label, got.get("player"), got.get("frames"))

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
                or len(entry["ref"]) > 512 or not isinstance(entry.get("label", ""), (str, type(None))):
            raise ValueError("each ghost is {ref, label}: a ref the catalog listed, and its label")
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
        return {"clip": data, "player": "You", "frames": frames,
                "label": ghost_label(SimpleNamespace(pb=False, import_id=None))}
