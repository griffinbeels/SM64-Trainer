"""EVERY GHOST SETTING, one witness per registry row (`ghost/settings.py`).

Each row is refused a bad value with his sentence, applied, remembered across
a reload of the session, and -- when it has native fields -- carried to the
native command. Each published limit is what the card reads, and each on/off
switch has words on the card. A new on/off row needs no edit here; a row of
another kind needs its two sample values in `SAMPLES`, and a guard fails
until it has them.
"""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sm64_events.ghost import settings
from test_ghost_pb import pb_world, save_pb

REPO = Path(__file__).resolve().parent.parent
GHOST_JS = (REPO / "src" / "sm64_events" / "ui" / "ghostsession.js").as_uri()

#: A changed value and a refused value for every row that is not a switch.
SAMPLES = {
    "enabled": (False, "yes"),
    "opacity": (0.8, 1.5),
    "tint": ({"hue_degrees": 200}, {"hue_degrees": 400}),
    "volume": (0.25, "loud"),
    "proximity": ({"extra_distance": 120.0}, {"extra_distance": 401}),
    "view": ("whole", "rotoscope"),
    "nudge_frames": (12, settings.NUDGE_LIMIT_FRAMES + 1),
}


def sample(row):
    return (not row.default, "yes") if row.switch else SAMPLES[row.name]


def test_every_row_has_samples():
    missing = [row.name for row in settings.ROWS if not row.switch and row.name not in SAMPLES]
    assert not missing, f"add a changed and a refused value for {missing}"


@pytest.mark.parametrize("row", settings.ROWS, ids=lambda row: row.name)
def test_a_setting_is_checked_remembered_and_reaches_the_renderer(tmp_path, row):
    session, _replay, pb, archive, db, _store, capture = pb_world(tmp_path)
    changed, refused = sample(row)

    async def check():
        await session.update({"enabled": True, "view": "mario"})
        save_pb(db, pb)
        await session.pb_changed(pb, True)
        session._native.note()
        before = session.payload()
        with pytest.raises(ValueError):
            await session.update({row.name: refused})
        assert session.payload() == before  # a refused request changes nothing
        expected = settings.applied(before[row.name], row, row.check(changed))
        body = await session.update({row.name: changed})
        session._native.note()
        assert body[row.name] == expected
        field = row.extra.get("native_field")
        if field:  # a switch the renderer reads carries its own field, whatever `native` says
            assert capture.commands[-1][field] is expected
        if row.native is not None:
            sent = capture.commands[-1]
            # A per-ghost field (the hue) rides on each clip; the first wears it unshifted.
            carried = {field: sent[field] if field in sent else sent["clips"][0][field]
                       for field in row.native(expected)}
            assert carried == row.native(expected)
        if row.remembered():
            # A fresh read of the database, as the next server start does it.
            session._loaded, session._values = False, settings.defaults()
            session._load_persisted()
            assert session.payload()[row.name] == expected
        await session.stop()

    try:
        asyncio.run(check())
    finally:
        archive.close()


def test_every_native_switch_is_a_channel_field():
    """The channel encodes each switch the registry names into the request;
    a field the protocol lacks would be a switch that never leaves Python."""
    from sm64_events.ghost import native_protocol
    fields = {name for name, *_ in native_protocol.Request._fields_}
    assert set(settings.native_switches()) <= fields


def run_node(body: str):
    script = (f"import {{ ghostControls, SWITCH_LABELS }} from {GHOST_JS!r};\n"
              f"console.log(JSON.stringify({body}));")
    result = subprocess.run(["node", "--input-type=module", "-"], input=script, capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_the_card_reads_every_published_limit_and_switch():
    """The card's numbers are the server's: a visibility below the offered
    floor, a colour past the wheel and a nudge past the limit are all drawn at
    the published edge, and every switch has words and reads its state."""
    limits = settings.limits()
    payload = {**settings.defaults(), "limits": limits, "opacity": 0, "nudge_frames": 99999,
               "tint": {"enabled": True, "hue_degrees": 999},
               "target": {"kind": "star", "entity_key": "8-1", "strat": None},
               "source": {"available": True}}
    for name in limits["switches"]:
        payload[name] = not settings.BY_NAME[name].default
    shown = run_node(f"{{controls: ghostControls({json.dumps(payload)}, 'star:8:1'), labels: SWITCH_LABELS}}")
    controls = shown["controls"]
    assert controls["limits"] == limits
    assert controls["opacity"] == limits["opacity"]["min"]
    assert controls["tint"]["hue"] == limits["tint"]["max"]
    assert controls["nudge"] == limits["nudge_frames"]["limit"]
    for name in limits["switches"]:
        assert name in shown["labels"], f"ui/ghostsession.js SWITCH_LABELS has no words for {name!r}"
        assert controls["switches"][name] is (not settings.BY_NAME[name].default)
