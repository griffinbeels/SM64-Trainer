# src/sm64_events/ghost/settings.py
"""THE GHOST'S SETTINGS, one row each.

Every preference the ghost card offers is one `Setting` row in `ROWS`: the
key a client writes in `PUT /api/ghost`, its default, the check that refuses
a bad value with the sentence he reads, the database key it is remembered
under, the native command fields it becomes, and what the card's control
offers (`limits`, published in `GET /api/ghost` so the page never restates a
number this module owns). The session, the native command, the test fixture
and the one parametrized test all walk these rows.

**Adding an on/off setting** is one `switch(...)` row here, plus one label row
in `ui/ghostsession.js` (`SWITCH_LABELS`), plus -- when the renderer
reads it -- the field in `ghost/native_protocol.py` and its native side. The
recipe and the file count are in `.claude/rules/ghost.md`.
"""
from dataclasses import dataclass, field
from typing import Any, Callable

from sm64_events.ghost.proximity import (
    DEFAULT_PROXIMITY, MAX_EXTRA_DISTANCE, PROXIMITY_KEY, checked_proximity, saved_proximity,
)
from sm64_events.ghost.native_layout import INPUT_STICK, INPUT_VALUES, NAME_BLACK, NAME_WHITE

#: How see-through the ghost is the FIRST time it is ever switched on. A
#: shipped preference, so its exact value is his to move and no test pins it --
#: what matters is that a first switch-on is see-through, because a solid
#: overlay is indistinguishable from the emulator having vanished (which is
#: what it looked like on 2026-09-19). A value he has already chosen wins.
DEFAULT_OPACITY = 0.5
#: The slider's own floor: a ghost at zero is a control that looks broken. The
#: server still accepts 0..1; this is what the control offers.
OPACITY_OFFERED_MIN = 0.15
#: HOW THE GHOST IS COLOURED, so it cannot be mistaken for the live picture.
#: His words, 2026-09-19: *"It feels really hard to understand which one is the
#: ACTUAL LIVE GAMEPLAY and which one is the GHOST gameplay... Maybe we can have
#: a color picker... The user can disable the color shift if they want."* So:
#: on by default, a third of the way round the colour wheel, and his to move or
#: switch off. A shipped preference, so no test pins these two numbers.
DEFAULT_TINT = {"enabled": True, "hue_degrees": 120}
#: One full turn of the colour wheel. 0 and 360 are the same colour, so the
#: control offers the half-open range and 360 is refused rather than silently
#: meaning "off".
HUE_LIMIT_DEGREES = 359
#: WHAT THE GHOST SHOWS. His words, 2026-09-20: *"let's do tight rectangles on
#: our existing ghosts, let's try it."* Default "whole", because that is the
#: ghost he already knows and most clips have no boxes to draw the other one from.
GHOST_VIEWS = ("whole", "mario")
DEFAULT_VIEW = "whole"
#: How far the ghost may be nudged, in game frames. 600 is twenty seconds at
#: 30 fps -- past the length of most of the stars this is used on, so the limit
#: only ever catches a typo or a stuck key. The native channel and renderer
#: bound the same number; the card reads it from `limits`.
NUDGE_LIMIT_FRAMES = 600
#: HOW LOUD GHOST MARIO IS. His words, 2026-09-22: *"We should have a volume
#: slider to adjust the ghost volume."* Half by default so the ghost never
#: drowns out the live game; a shipped preference.
DEFAULT_VOLUME = 0.5
INPUT_MODES = {"stick": INPUT_STICK, "values": INPUT_VALUES}
DEFAULT_INPUT_MODE = "stick"
#: THE BOX BEHIND THE GHOST'S NAME. His words, 2026-10-02: *"The nametag should
#: have a black background like this (the text for talking to the whomp king)
#: with adjustable opacity (probably 50% but not sure). Add another option to
#: have a white background like this (the text for the sign)"*. The game's own
#: boxes are black or white at alpha 150 (ingame_menu.c render_dialog_box_type).
NAME_BACKGROUNDS = {"black": NAME_BLACK, "white": NAME_WHITE}
DEFAULT_NAME_BACKGROUND = "black"
DEFAULT_NAME_BACKGROUND_OPACITY = 0.6
#: The least of its opacity a ghost keeps when it overlaps or blocks Mario
#: (plugin/gfxwrap/ghost_occlusion.h GHOST_FLOOR). Published so the card's
#: sentence about it is not a second copy of the number.
OVERLAP_FLOOR = 0.15


@dataclass(frozen=True)
class Setting:
    """One preference. `check` turns what a client sent into the value to
    apply, or raises ValueError with the sentence he reads; `restore` turns
    whatever the database holds into a usable value and never raises."""
    name: str
    default: Any
    check: Callable[[Any], Any]
    key: str | None = None
    restore: Callable[[Any], Any] | None = None
    native: Callable[[Any], dict] | None = None
    limits: dict | None = None
    merge: bool = False
    switch: bool = False
    #: A change to it moves what the ghost resolve answers (which ghosts can
    #: play); the rest ride the clock feed or the native command and never do.
    resolves: bool = False
    #: Custom persistence for a value kept under more than one key.
    load: Callable[[Any], Any] | None = None
    store: Callable[[Any, Any], None] | None = None
    extra: dict = field(default_factory=dict)

    def remembered(self) -> bool:
        return self.key is not None or self.store is not None

    def read(self, database) -> Any:
        if self.load is not None:
            return self.load(database)
        return self.restore(database.get_state(self.key, self.default))

    def write(self, database, value) -> None:
        if self.store is not None:
            self.store(database, value)
        else:
            database.set_state(self.key, value)


def _checked_bool(sentence: str) -> Callable[[Any], bool]:
    def check(value):
        if type(value) is not bool:
            raise ValueError(sentence)
        return value
    return check


def switch(name: str, key: str, default: bool, sentence: str, native_field: str | None) -> Setting:
    """An on/off setting: a bool, remembered, its own native field (or none)."""
    return Setting(name=name, default=default, check=_checked_bool(sentence), key=key,
                   restore=lambda stored: stored if type(stored) is bool else default,
                   native=(lambda value: {native_field: bool(value)}) if native_field else None,
                   switch=True, extra={"native_field": native_field})


def _clamped(default: float) -> Callable[[Any], float]:
    # Coerced rather than trusted: a stored value is whatever the last version
    # of this code wrote, and a nonsense one must not make the window unopenable.
    def restore(stored):
        try:
            number = float(stored)
        except (TypeError, ValueError):
            return default
        return min(1.0, max(0.0, number))
    return restore


def checked_opacity(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("How see-through the ghost is has to be a number between 0 and 1.")
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError("How see-through the ghost is has to be between 0 (invisible) and 1 (solid).")
    return float(value)


def checked_nudge(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("The nudge is a whole number of game frames.")
    if abs(value) > NUDGE_LIMIT_FRAMES:
        raise ValueError(f"The ghost can only be nudged up to {NUDGE_LIMIT_FRAMES} frames "
                         "(about twenty seconds) either way.")
    return value


def checked_tint(value) -> dict:
    """The colour treatment, as a PARTIAL: a client may send either half.

    Sending only the angle must not switch the treatment off and sending only
    the switch must not throw away the angle he chose, so this returns exactly
    the fields it was given and the session merges them over what is set.
    """
    if not isinstance(value, dict):
        raise ValueError("The ghost's colour is a switch and an angle.")
    unknown = sorted(set(value) - {"enabled", "hue_degrees"})
    if unknown:
        raise ValueError(f"The ghost's colour has no setting called {unknown[0]!r}.")
    checked = {}
    if "enabled" in value:
        if not isinstance(value["enabled"], bool):
            raise ValueError("The ghost's colour shift is either on or off.")
        checked["enabled"] = value["enabled"]
    if "hue_degrees" in value:
        hue = value["hue_degrees"]
        if isinstance(hue, bool) or not isinstance(hue, int):
            raise ValueError("The ghost's colour is a whole number of degrees around the colour wheel.")
        if not 0 <= hue <= HUE_LIMIT_DEGREES:
            raise ValueError(f"The ghost's colour goes from 0 to {HUE_LIMIT_DEGREES} "
                             "degrees around the colour wheel.")
        checked["hue_degrees"] = hue
    return checked


def checked_view(value) -> str:
    """Asking for "mario" on a ghost whose clip has no boxes is NOT an error:
    the setting is app-wide and he walks between stars, so refusing it would
    make the control jam on the first ghost without boxes."""
    if value not in GHOST_VIEWS:
        raise ValueError("The ghost shows either the whole picture or only Mario.")
    return value


def checked_volume(volume) -> float:
    if type(volume) not in (int, float) or not 0 <= volume <= 1:
        raise ValueError("The ghost's volume is between silent and full.")
    return float(volume)


def checked_input_mode(value) -> str:
    if not isinstance(value, str) or value not in INPUT_MODES:
        raise ValueError("Show the ghost's stick as a picture or as raw values.")
    return value


def checked_name_background(value) -> str:
    if not isinstance(value, str) or value not in NAME_BACKGROUNDS:
        raise ValueError("The box behind the ghost's name is either black or white.")
    return value


def checked_name_background_opacity(value) -> float:
    if type(value) not in (int, float) or not 0 <= value <= 1:
        raise ValueError("How solid the box behind the ghost's name is goes from 0 (no box) to 1 (solid).")
    return float(value)


def _load_tint(database) -> dict:
    enabled = database.get_state("ghost_tint_enabled", DEFAULT_TINT["enabled"])
    stored = database.get_state("ghost_tint_hue", DEFAULT_TINT["hue_degrees"])
    try:
        hue = int(stored) % (HUE_LIMIT_DEGREES + 1)
    except (TypeError, ValueError):
        hue = DEFAULT_TINT["hue_degrees"]
    return {"enabled": bool(enabled), "hue_degrees": hue}


def _store_tint(database, tint: dict) -> None:
    database.set_state("ghost_tint_enabled", tint["enabled"])
    database.set_state("ghost_tint_hue", tint["hue_degrees"])


def _native_proximity(value: dict) -> dict:
    proximity = {**DEFAULT_PROXIMITY, **checked_proximity(value or {})}
    return {"proximity_enabled": proximity["enabled"], "proximity_distance": proximity["extra_distance"]}


#: EVERY GHOST PREFERENCE, in payload order. `enabled` reaches the renderer
#: through the session itself, which decides what "enabled" means for the
#: current target and view, so its row carries no native field.
ROWS = (
    Setting("enabled", False, _checked_bool("The ghost overlay is either on or off."), resolves=True,
            key="ghost_enabled", restore=bool),
    Setting("opacity", DEFAULT_OPACITY, checked_opacity, key="ghost_opacity",
            restore=_clamped(DEFAULT_OPACITY), native=lambda value: {"opacity": value},
            limits={"min": OPACITY_OFFERED_MIN, "max": 1.0, "step": 0.05}),
    Setting("tint", dict(DEFAULT_TINT), checked_tint, merge=True, load=_load_tint, store=_store_tint,
            native=lambda value: {"tint_enabled": value["enabled"], "hue_degrees": value["hue_degrees"]},
            limits={"min": 0, "max": HUE_LIMIT_DEGREES, "step": 1}),
    # THE GHOST'S SHADOW. His words, 2026-09-22: *"It should be the same type of
    # shadow that follows mario ... and then make sure we can easily A/B test
    # whether we like this or not."* On: the shadow is the game's own.
    switch("shadow", "ghost_shadow", True, "The ghost's shadow is either on or off.", "shadow_enabled"),
    switch("names_enabled", "ghost_names", True,
           "The ghost's name is either shown or hidden.", "names_enabled"),
    Setting("name_background", DEFAULT_NAME_BACKGROUND, checked_name_background, key="ghost_name_background",
            restore=lambda stored: stored if isinstance(stored, str) and stored in NAME_BACKGROUNDS
            else DEFAULT_NAME_BACKGROUND,
            native=lambda value: {"name_background": NAME_BACKGROUNDS[value]},
            limits={"options": list(NAME_BACKGROUNDS)}),
    Setting("name_background_opacity", DEFAULT_NAME_BACKGROUND_OPACITY, checked_name_background_opacity,
            key="ghost_name_background_opacity", restore=_clamped(DEFAULT_NAME_BACKGROUND_OPACITY),
            native=lambda value: {"name_background_opacity": float(value)},
            limits={"min": 0.0, "max": 1.0, "step": 0.05}),
    switch("inputs_enabled", "ghost_inputs", False,
           "The ghost's inputs are either shown or hidden.", "inputs_enabled"),
    Setting("input_mode", DEFAULT_INPUT_MODE, checked_input_mode, key="ghost_input_mode",
            restore=lambda stored: stored if isinstance(stored, str) and stored in INPUT_MODES else DEFAULT_INPUT_MODE,
            native=lambda value: {"input_mode": INPUT_MODES[value]},
            limits={"options": list(INPUT_MODES)}),
    # THE GHOSTS' INPUTS IN THE CORNER. His words, 2026-10-02: *"I want this as a
    # separate toggle. If we toggle this on, then it would add the input overlay
    # for each ghost above the bottom left pre-existing input overlay."* Off, so
    # the HUD he already knows is unchanged until he asks for the stack.
    switch("hud_inputs_enabled", "ghost_hud_inputs", False,
           "The ghosts' inputs above your own input display are either shown or hidden.",
           "hud_inputs_enabled"),
    # THE GHOST NEVER BLOCKS THE PLAYER (the player-first fade). His words,
    # 2026-09-23: *"if the ghost is blocking the player in any way, we
    # prioritize rendering the player, not the ghost."* On; his A/B switch.
    switch("occlusion_fade", "ghost_occlusion_fade", True,
           "Fading the ghost in front of Mario is either on or off.", "occlusion_fade"),
    Setting("volume", DEFAULT_VOLUME, checked_volume, key="ghost_volume",
            restore=_clamped(DEFAULT_VOLUME), native=lambda value: {"volume": float(value)},
            limits={"min": 0.0, "max": 1.0, "step": 0.05}),
    Setting("proximity", dict(DEFAULT_PROXIMITY), checked_proximity, key=PROXIMITY_KEY,
            restore=saved_proximity, merge=True, native=_native_proximity,
            limits={"min": 0.0, "max": MAX_EXTRA_DISTANCE, "step": 10, "floor": OVERLAP_FLOOR}),
    # THE GHOST'S SOUND FADES WITH ITS PICTURE. His words, 2026-09-28: *"If the
    # ghost is more invisible, the audio should be quieter, too. However, I also
    # don't want to lose the benefits of the ghost audio"*, and of the fade in
    # front of Mario: *"yes, we should do this for the camera blocking case,
    # too."* On: its sound follows every fade its picture has, and while it
    # overlaps him a sound Mario makes on the same frame plays once
    # (plugin/gfxwrap/ghost_sound_fade.h). The key keeps its first name, so a
    # choice he already made survives.
    switch("sound_fade", "ghost_overlap_sound", True,
           "Fading the ghost's sound with its picture is either on or off.", "sound_fade"),
    # A stored value from a build that knew another view name is not a view:
    # it falls back rather than leaving the page asking for what nothing draws.
    Setting("view", DEFAULT_VIEW, checked_view, key="ghost_view", resolves=True,
            restore=lambda stored: stored if stored in GHOST_VIEWS else DEFAULT_VIEW),
    # Never remembered: it belongs to the target, gone by the next session.
    Setting("nudge_frames", 0, checked_nudge, native=lambda value: {"nudge": value},
            limits={"limit": NUDGE_LIMIT_FRAMES}),
)
BY_NAME = {row.name: row for row in ROWS}


def defaults() -> dict:
    return {row.name: copied(row.default) for row in ROWS}


def limits() -> dict:
    """What every control offers, plus which settings are on/off switches."""
    published = {row.name: dict(row.limits) for row in ROWS if row.limits}
    published["switches"] = [row.name for row in ROWS if row.switch]
    return published


def validated(changes: dict) -> dict:
    """The registry's half of `validate_changes`: every row present, checked."""
    return {name: BY_NAME[name].check(value) for name, value in changes.items() if name in BY_NAME}


def applied(current: Any, row: Setting, checked: Any) -> Any:
    """The value a row holds after a checked change: merged for partial rows."""
    return {**current, **checked} if row.merge else checked


def native_controls(values: dict) -> dict:
    """Every row's fields in the native command."""
    controls = {}
    for row in ROWS:
        if row.native is not None:
            controls.update(row.native(values[row.name]))
    return controls


def resolving() -> frozenset:
    """The settings whose change asks the ghost resolve again."""
    return frozenset(row.name for row in ROWS if row.resolves)


def native_switches() -> dict:
    """The native bool fields the channel decodes, with the value an absent key means."""
    return {row.extra["native_field"]: row.default for row in ROWS
            if row.switch and row.extra.get("native_field")}


def copied(value):
    return dict(value) if isinstance(value, dict) else value
