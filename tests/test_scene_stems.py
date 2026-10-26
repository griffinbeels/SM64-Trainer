"""Music and effects stems: which command writes whose note into the mix (tools/scene_lab/stems_probe.py).

A stem renders the game's own command list with the other side's notes withheld from the four mix
buffers. These tests hold the partition on synthetic lists shaped like synthesis.c's: a reverb
return at each update's start, notes named by their state addresses, and a voice the sequencer
hands from a music player to the sound effect player in the middle of a frame. The rendering
itself needs the ROM's microcode and a savestate: stems_probe.py run proves it on private data.
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/scene_lab"))
spec = importlib.util.spec_from_file_location("scene_stems", ROOT / "tools/scene_lab/stems_probe.py")
stems = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = stems
spec.loader.exec_module(stems)

RING = (0x1D8E00, 0x1DBE00)
MUSIC, EFFECTS = (0, 3), (2, 3)
BUFFERS = {0: 0x801D7600, 1: 0x801D7800}


def cmd(op: int, flags: int = 0, low: int = 0, w2: int = 0) -> tuple[int, int]:
    return (op << 24) | (flags << 16) | low, w2


def note_commands(note: int, start: bool = False, stereo_strong: bool = False) -> list[tuple[int, int]]:
    base = BUFFERS[note] & 0x1FFFFFFF
    init = 1 if start else 0
    out = [cmd(stems.A_ADPCM, init, 0, base), cmd(stems.A_RESAMPLE, init, 0x4000, base + 0x20),
           cmd(stems.A_SETBUFF, 0, 0x000, (0x4C0 << 16) | 0x110),
           cmd(stems.A_SETBUFF, 0x08, 0x600, (0x740 << 16) | 0x880),
           cmd(stems.A_SETVOL, 0x06, 0x1000), cmd(stems.A_SETVOL, 0x08, 0x7FFF, 0),
           cmd(stems.A_ENVMIXER, 0x08 | init, 0, base + 0x40)]
    if stereo_strong:
        out += [cmd(stems.A_SETBUFF, 0, 0, 0x110), cmd(stems.A_MIXER, 0, 0x8000, (0x200 << 16) | 0x4C0)]
    return out


def update_start() -> list[tuple[int, int]]:
    return [cmd(stems.A_LOADBUFF, 0, 0, RING[0]), cmd(stems.A_DMEMMOVE, 0, 0x740, (0x4C0 << 16) | 0x280),
            cmd(stems.A_MIXER, 0, 0xAFFF, (0x740 << 16) | 0x740)]


def frame() -> list[tuple[int, int]]:
    """Update 0: note 0 (music) and note 1 still releasing for music; update 1: note 1 restarts as an effect."""
    return (update_start() + note_commands(0) + note_commands(1) + [cmd(stems.A_SAVEBUFF, 0, 0, RING[0])]
            + update_start() + note_commands(0) + note_commands(1, start=True, stereo_strong=True)
            + [cmd(stems.A_SAVEBUFF, 0, 0, RING[0])])


def notes() -> dict[int, dict]:
    return {0: {"buffers": BUFFERS[0], "owner": MUSIC, "previous": MUSIC, "enabled": True},
            1: {"buffers": BUFFERS[1], "owner": EFFECTS, "previous": (0, 5), "enabled": True}}


def test_each_mix_writer_gets_the_owner_of_its_note_at_that_point_in_the_frame():
    owners, stats = stems.attribute(frame(), notes(), RING)
    writers = [(i, owner) for i, owner in enumerate(owners) if owner is not None]
    assert [owner for _, owner in writers] == [MUSIC, (0, 5), MUSIC, EFFECTS, EFFECTS]
    assert stats["starts"] == 1 and stats["handovers"] == [(1, (0, 5), EFFECTS)]
    reverb_gain = [i for i, (w1, _) in enumerate(frame()) if w1 >> 24 == stems.A_MIXER and w1 & 0xFFFF == 0xAFFF]
    assert all(owners[i] is None for i in reverb_gain)      # each stem keeps its own reverb return


def test_music_stem_withholds_only_the_effect_side_of_the_handed_over_voice():
    owners, _ = stems.attribute(frame(), notes(), RING)
    music = stems.muted_mask(owners, stems.MUSIC_PLAYERS)
    effects = stems.muted_mask(owners, stems.EFFECT_PLAYERS)
    writers = [i for i, owner in enumerate(owners) if owner is not None]
    assert [int(music[i]) for i in writers] == [0, 0, 0, 1, 1]
    assert [int(effects[i]) for i in writers] == [1, 1, 1, 0, 0]
    assert not np.any(music & effects)                      # every writer lands in exactly one stem


def test_unknown_owner_is_withheld_from_both_stems():
    table = notes()
    table[0] = dict(table[0], owner=None, previous=None)
    owners, _ = stems.attribute(frame(), table, RING)
    assert owners.count("unknown") == 2
    both = stems.muted_mask(owners, stems.MUSIC_PLAYERS) & stems.muted_mask(owners, stems.EFFECT_PLAYERS)
    assert int(both.sum()) == 2


def test_edited_list_sends_a_muted_notes_mixing_to_scratch_and_keeps_everything_else():
    cmds = frame()
    owners, _ = stems.attribute(cmds, notes(), RING)
    mask = stems.muted_mask(owners, stems.MUSIC_PLAYERS)
    edited = stems.edited_list(cmds, mask)
    changed = [i for i, (a, b) in enumerate(zip(cmds, edited, strict=True)) if a != b]
    envmixer = next(i for i in np.flatnonzero(mask) if cmds[i][0] >> 24 == stems.A_ENVMIXER)
    mixer = next(i for i in np.flatnonzero(mask) if cmds[i][0] >> 24 == stems.A_MIXER)
    assert changed == [envmixer - 4, envmixer - 3, mixer]   # its two buffer settings, its own mix
    scratch = stems.SCRATCH_DMEM
    assert edited[envmixer - 4] == (cmds[envmixer - 4][0], (scratch << 16) | 0x110)
    assert edited[envmixer - 3] == ((cmds[envmixer - 3][0] & 0xFFFF0000) | scratch, (scratch << 16) | scratch)
    assert edited[mixer] == (0, 0)                          # A_SPNOOP
    assert edited[envmixer] == cmds[envmixer]               # the mixer still runs and saves its state


def test_edited_list_refuses_an_envelope_mixer_without_its_buffer_settings():
    base = BUFFERS[0] & 0x1FFFFFFF
    cmds = [cmd(stems.A_RESAMPLE, 0, 0x4000, base + 0x20), cmd(stems.A_ENVMIXER, 0, 0, base + 0x40)]
    with pytest.raises(ValueError):
        stems.edited_list(cmds, np.array([0, 1], np.uint8))
