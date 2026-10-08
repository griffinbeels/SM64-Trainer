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
    "nudge_frames": (12, settings.NUDGE_LIMIT_FRAMES + 1),
    "input_mode": ("values", "combined"),
    "name_background": ("white", "grey"),
    "name_background_opacity": (0.25, 1.5),
    "path_source": ("fastest", "middle"),
    "path_tuning": ({"width": 60}, {"width": 500}),
}


def sample(row):
    return (not row.default, "yes") if row.switch else SAMPLES[row.name]


def test_every_row_has_samples():
    missing = [row.name for row in settings.ROWS if not row.switch and row.name not in SAMPLES]
    assert not missing, f"add a changed and a refused value for {missing}"


def test_every_shipped_default_is_valid():
    """Tuning may change; each default must still satisfy its own contract."""
    for row in settings.ROWS:
        assert row.check(row.default) == row.default


def test_paths_default_off_while_explicit_saved_choice_wins():
    row=settings.BY_NAME["path_enabled"]
    class Database:
        def __init__(self,state):
            self.state=state
        def get_state(self,key,default):
            return self.state.get(key,default)
    assert settings.defaults()["path_enabled"] is False
    assert row.read(Database({})) is False
    assert row.read(Database({row.key:True})) is True
    assert row.read(Database({row.key:False})) is False


def test_retired_view_is_not_loaded_or_written():
    """Retiring a renderer leaves saved preferences in their original keys."""
    class Database:
        state = {"ghost_view": "whole", "ghost_enabled": False, "ghost_opacity": 0.25}

        def get_state(self, key, default):
            return self.state.get(key, default)

    database = Database()
    before = dict(database.state)
    restored = {row.name: row.read(database) for row in settings.ROWS if row.remembered()}
    assert "view" not in restored
    assert restored["enabled"] is False and restored["opacity"] == 0.25
    assert database.state == before


@pytest.mark.parametrize("row", settings.ROWS, ids=lambda row: row.name)
def test_a_setting_is_checked_remembered_and_reaches_the_renderer(tmp_path, row):
    session, _replay, pb, archive, db, _store, capture = pb_world(tmp_path)
    changed, refused = sample(row)

    async def check():
        await session.update({"enabled": True})
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


# -- one definition of each default ------------------------------------------

#: Settings whose default has been restated beside the registry before (the
#: native channel and session each carried their own 0.5 for both).
GUARDED = ("opacity", "volume")
#: The shipped modules a ghost setting passes through.
GHOST_MODULES = sorted([*(REPO / "src/sm64_events/ghost").glob("*.py"),
                        REPO / "src/sm64_events/server/ghost_api.py"])


def restated_defaults(source: str, names=GUARDED) -> list[str]:
    """Every place a module gives a guarded setting its own literal value:
    `DEFAULT_<NAME> = ...`, `x.get("<name>", <literal>)`, a parameter named
    after it with a literal default, or `<obj>.<name> = <literal>`."""
    import ast
    found = []
    literal = lambda node: isinstance(node, ast.Constant) and node.value is not None  # noqa: E731
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign):
            targets = [element for target in node.targets
                       for element in (target.elts if isinstance(target, ast.Tuple) else [target])]
            values = node.value.elts if isinstance(node.value, ast.Tuple) else [node.value] * len(targets)
            for target, value in zip(targets, values, strict=False):
                name = getattr(target, "id", None) or getattr(target, "attr", None)
                if name in {f"DEFAULT_{guarded.upper()}" for guarded in names}:
                    found.append(f"line {node.lineno}: {name} defined")
                elif name in names and literal(value):
                    found.append(f"line {node.lineno}: {name} = {value.value!r}")
        elif (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "get"
              and len(node.args) == 2 and isinstance(node.args[0], ast.Constant)
              and node.args[0].value in names and literal(node.args[1])):
            found.append(f"line {node.lineno}: .get({node.args[0].value!r}, {node.args[1].value!r})")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = node.args
            positional = arguments.args[len(arguments.args) - len(arguments.defaults):]
            pairs = [*zip(positional, arguments.defaults, strict=True),
                     *zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True)]
            found += [f"line {node.lineno}: {node.name}({arg.arg}={default.value!r})"
                      for arg, default in pairs if default is not None and arg.arg in names and literal(default)]
    return found


def test_only_the_registry_gives_a_setting_its_default():
    """The default visibility and volume are the registry rows' own; the
    native channel, the native session and the session read them there
    (`settings.DEFAULT_OPACITY`, `settings.DEFAULT_VOLUME`) or are handed the
    value, so moving a default is one edit."""
    offenders = {}
    for path in GHOST_MODULES:
        if path.name == "settings.py":
            continue
        found = restated_defaults(path.read_text(encoding="utf-8"))
        if found:
            offenders[path.relative_to(REPO).as_posix()] = found
    assert not offenders, f"these modules restate a registry default: {offenders}"
    assert restated_defaults((REPO / "src/sm64_events/ghost/settings.py").read_text(encoding="utf-8")), \
        "the owner itself must define them, or the scan could never match anything"


@pytest.mark.parametrize("planted", [
    'OPACITY = value.get("opacity", 0.5)\n',
    "DEFAULT_VOLUME = 0.5\n",
    "def reconcile(*, volume=0.5):\n    pass\n",
    "request.opacity, request.volume = 0.5, DEFAULT_VOLUME\n",
])
def test_the_default_guard_can_still_fail(planted):
    assert restated_defaults(planted)


def test_the_default_guard_ignores_what_is_not_a_default():
    assert not restated_defaults('request.opacity = DEFAULT_OPACITY\nwait = value.get("opacity")\n'
                                 'WATCH_PERIOD_S = 0.5\ndef reconcile(*, volume=None):\n    pass\n')
