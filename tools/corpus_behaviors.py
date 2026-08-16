"""Every behavior script in the ROM, as a name the recorder can say.

His ask (round 8 item 2, 2026-08-07): *"looks like when we pick up the
bob-omb, it's 'an object' -- same for bowser's tail, it's 'an object'. Is
there a way to automatically detect WHAT the object is?"* The landmark
already carries the identity -- the behaviour SYMBOL since 2026-08-15, the
behaviour POINTER before that (`core/landmark.py`) -- and the table that
turns it into a noun now lives IN THE APP: `memory/behaviours.py`, with both
STROOP maps' behaviour segments shipped verbatim as
`src/sm64_events/data/behaviours_{us,jp}.tsv` (`tools/import_stroop_maps.py`
regenerates them from the maps). This module is the corpus tools' door onto
it and nothing more; the base anchoring (his 2026-08-07 bob-omb, 8 of 8
touched objects resolving to what he touched), the name-case grammar and the
override tables are all documented where they now live.

The one open check that used to sit here -- Usamune's US base never read out
of its own RAM live -- is a gate now (`sync/address_gates.py::behaviour.base`)
and closes on the first US baseline run.
"""
from sm64_events.memory.behaviours import (NAME_OVERRIDES, WORD_FORMS,  # noqa: F401
                                           display_name, kind_names, offsets,
                                           pointer_of)
from sm64_events.memory.layout import US as _US

# US, for callers that still speak pointers (tests, one-off scripts).
BEHAVIOR_SEGMENT_BASE_US = _US.behaviour_base


def kind_names_us_pointers():
    """(US RAM pointer, shipped name) -- the pre-2026-08-15 shape, for a
    caller that has a pointer in hand rather than a symbol."""
    for symbol, name in kind_names("us"):
        yield pointer_of("us", symbol, base=BEHAVIOR_SEGMENT_BASE_US), name


# The map's behavior-segment rows, verbatim: (segmented address, symbol).
BEHAVIORS: tuple[tuple[int, str], ...] = tuple(
    (segmented, symbol) for symbol, segmented in offsets("us").items())
