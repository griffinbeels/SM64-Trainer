"""A Scene viewer picture without the HUD (tools/scene_lab/hud.py), on synthetic lists.

The lists mirror the master list SM64 builds for a picture (hud.py's docstring): a prologue, the
world from geo_process_root (its own viewport, then its matrix), render_game's viewport and
scissor, the HUD, the text labels, the menus with an Usamune-like box in expansion RAM, the final
scissor, a fade's transition and the black borders. No ROM, RAM image, GPU or emulator is
involved; the routes run with fake_viewer_host.py as the renderer.
"""
import hashlib
import importlib.util
import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import fake_viewer_host  # noqa: E402
import gpu_gate  # noqa: E402
import hud  # noqa: E402
import pick  # noqa: E402
import scene_session  # noqa: E402
from sm64_events.server import scene_lab_api  # noqa: E402

spec = importlib.util.spec_from_file_location("scene_package_synthetic_hud", ROOT / "tests/test_scene_package.py")
synthetic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(synthetic)
pk = synthetic.pk

LIST, DATA, SEG2, WORLD, EXPANSION = 0x208000, 0x209000, 0x240000, 0x250000, 0x380000
RUNS = [(LIST, LIST + 0x400), (DATA, DATA + 0x200), (SEG2, SEG2 + 0x200), (WORLD, WORLD + 0x40),
        (EXPANSION, EXPANSION + 0x100)]
BORDER, FULL = (0xED000020, 0x005003A0), (0xED000000, 0x005003C0)
PIPESYNC, ENDDL = (0xE7000000, 0), (0xB8000000, 0)
TRI, VTX = (0xBF000000, 0x000A14), (0x04300040, DATA + 0x80)


def _write(ram: bytearray, at: int, commands) -> None:
    raw = b"".join(struct.pack(">II", *command) for command in commands)
    ram[at:at + len(raw)] = raw


def _call(address: int, branch: bool = False) -> tuple:
    return 0x06010000 if branch else 0x06000000, address


def hud_list(world: bool = True, transition: bool = True, change=None):
    """(big-endian RDRAM, the master list's address, its size, the addresses the cut must blank,
    the HUD's span). `change(commands)` edits the master list's (w0, w1, blank) rows first."""
    ram = bytearray(synthetic.RDRAM)
    _write(ram, SEG2, [PIPESYNC, (0xBA001402, 0x00300000), ENDDL])                    # HUD begin: state
    _write(ram, SEG2 + 0x40, [(0xF5100000, 0x07000000), (0xE6000000, 0), (0xF3000000, 0x0703F400), ENDDL])
    _write(ram, SEG2 + 0x80, [PIPESYNC, (0xBB000000, 0xFFFFFFFF), ENDDL])            # HUD end: state
    _write(ram, SEG2 + 0xC0, [TRI, (0xBF000000, 0x001E28), ENDDL])                    # a quad: draws
    _write(ram, SEG2 + 0x100, [PIPESYNC, _call(0x020000C0), ENDDL])                  # state, then a call that draws
    _write(ram, SEG2 + 0x140, [PIPESYNC, (0xB6000000, 0x00020000), (0x01030040, DATA + 0x40), ENDDL])
    _write(ram, WORLD, [VTX, TRI, ENDDL])
    _write(ram, EXPANSION, [(0xB6000000, 0x00000001), VTX, TRI, (0xBF000000, 0x001E28), ENDDL])  # a box
    _write(ram, EXPANSION + 0x80, [PIPESYNC, (0xFC127E24, 0xFFFFF3F9), ENDDL])        # its text's state
    ram[DATA:DATA + 16] = struct.pack(">8h", 640, 480, 511, 0, 640, 480, 511, 0)
    rect = [(0xE4464370, 0x00428334, True), (0xB3000000, 0, True), (0xB2000000, 0x10000400, True)]
    rows = [(0xBC000806, SEG2, False), (0xBC003806, WORLD, False), (*FULL, False), (0xF64FC39C, 0x20, False)]
    if world:
        rows += [(0x03800010, DATA + 0x20, False), (0x01020040, DATA + 0x40, False), (*_call(0x0E000000), False)]
        rows += [(0x03800010, DATA, False), (*BORDER, False),                         # render_game
                 (0x01020040, DATA + 0x40, False), (0xB4000000, 0xFFFF, False), (0x01010040, DATA + 0x40, False),
                 (*_call(0x02000000), False), (0xFD100000, 0x02007000, False), (*_call(0x02000040), False),
                 *rect, (*_call(0x02000080), False),                                  # render_hud
                 (*FULL, False), (0xE5464370, 0x00428334, True), (0xB3000000, 0, True),
                 (0xB2000000, 0x10000400, True),                                      # text labels
                 (*BORDER, False), (0x01040040, DATA + 0x40, False), (*_call(0x80000000 | EXPANSION), True),
                 (0xBD000000, 0, False), (*_call(0x80000000 | EXPANSION + 0x80), False),
                 (*_call(0x02000100), True), (*VTX, False), (*TRI, True), (0xF64FC01C, 0x00100010, True),
                 (*BORDER, False)]                                                    # the menus, the final scissor
        if transition:
            rows += [(*_call(0x02000140), False), (*VTX, False), (*_call(0x020000C0), False)]
    else:
        rows += [(*BORDER, False), (0xF7000000, 0x00010001, False), (0xF64FC39C, 0x20, False)]   # a clear
    rows += [(*PIPESYNC, False), (*FULL, False), (0xB900031D, 0x0F0A4000, False), (0xBA001402, 0x00300000, False),
             (0xF7000000, 0, False), (0xF64FC01C, 0, False), (0xF64FC3BC, 0x3A0, False), (0xE9000000, 0, False),
             (*ENDDL, False)]                                                         # draw_screen_borders
    if change:
        change(rows)
    _write(ram, LIST, [(w0, w1) for w0, w1, _blank in rows])
    blanked = [LIST + 8 * at for at, row in enumerate(rows) if row[2]]
    viewports = [at for at, row in enumerate(rows) if row[0] == 0x03800010 and rows[at + 1][0] >> 24 == 0xED]
    scissors = [at for at, row in enumerate(rows) if row[0] >> 24 == 0xED]
    span = (LIST + 8 * (viewports[-1] + 2), LIST + 8 * scissors[-2]) if viewports else None
    return ram, LIST, 8 * len(rows), blanked, span


def _closure(ram: bytes) -> bytes:
    """RUNS' bytes in Project64's word order, as a recorded frame carries them."""
    return b"".join(pk.swap_words(bytes(ram[begin:end])) for begin, end in RUNS)


def test_the_cut_blanks_every_hud_draw_and_keeps_every_state_command():
    """Texture rectangles with their halves, a rectangle fill, a triangle, a box in expansion RAM
    and a state list whose call draws: no-ops. The HUD's begin and end lists, its texture load,
    matrices and scissors stay, and so does everything outside the span: the world's draw before
    it, the transition's draws and the borders after it."""
    ram, at, size, expected, span = hud_list()
    kept = bytes(ram)
    cut = hud.cut(hud.ram_words(ram), 0x80000000 | at, size)
    assert cut == hud.Cut(span, tuple(expected), None)
    without = hud.blank_ram(ram, cut.blanked)
    assert bytes(ram) == kept and len(without) == len(ram)                      # a copy; nothing moves
    changed = np.flatnonzero(np.frombuffer(without, ">u8") != np.frombuffer(ram, ">u8")) * 8
    assert changed.tolist() == expected
    assert all(without[address:address + 8] == hud.NOOP for address in expected)


def test_a_recorded_closure_and_a_whole_ram_cut_the_same_commands():
    ram, at, size, expected, _span = hud_list()
    run_bytes = _closure(ram)
    cut = hud.cut(hud.closure_words(RUNS, run_bytes), at, size)
    assert cut == hud.cut(hud.ram_words(ram), at, size) and cut.blanked == tuple(expected)
    rebuilt = pick.ram_from_runs(RUNS, hud.blank_closure(RUNS, run_bytes, cut.blanked))
    without = hud.blank_ram(ram, cut.blanked)
    assert all(rebuilt[begin:end] == without[begin:end] for begin, end in RUNS)
    assert hud.blank_closure(RUNS, run_bytes, ()) is run_bytes


def test_a_list_without_the_world_has_no_hud_to_cut():
    """A level load or the black after a fade: render_game draws no world, and geo_process_root's
    own viewport (followed by its matrix, not a scissor) is not where the HUD starts."""
    ram, at, size, expected, span = hud_list(world=False)
    assert expected == [] and span is None
    assert hud.cut(hud.ram_words(ram), at, size) == hud.Cut(None, (), None)

    def geo_only(rows):
        rows[4:4] = [(0x03800010, DATA + 0x20, False), (0x01020040, DATA + 0x40, False)]
    ram, at, size, _expected, _span = hud_list(world=False, change=geo_only)
    assert hud.cut(hud.ram_words(ram), at, size) == hud.Cut(None, (), None)


def test_a_fade_needs_no_transition_to_find_the_final_scissor():
    ram, at, size, expected, span = hud_list(transition=False)
    assert hud.cut(hud.ram_words(ram), at, size) == hud.Cut(span, tuple(expected), None)


def _branch_in_hud(rows):
    at = next(at for at, row in enumerate(rows) if row[1] == 0x02000080)
    rows[at] = (*_call(0x02000080, branch=True), False)


def _end_in_hud(rows):
    at = next(at for at, row in enumerate(rows) if row[1] == 0x02000080)
    rows[at] = (*ENDDL, False)


def _one_scissor_after_the_viewport(rows):
    first = next(at for at, row in enumerate(rows) if row[:2] == (0x03800010, DATA)) + 2
    last = max(at for at, row in enumerate(rows) if row[0] >> 24 == 0xED)
    rows[:] = [row for at, row in enumerate(rows) if not (first <= at < last and row[0] >> 24 == 0xED)]


@pytest.mark.parametrize("change", [_branch_in_hud, _end_in_hud, _one_scissor_after_the_viewport])
def test_a_list_not_shaped_like_the_games_keeps_its_hud_and_says_why(change):
    ram, at, size, _expected, _span = hud_list(change=change)
    assert hud.cut(hud.ram_words(ram), at, size) == hud.Cut(None, (), hud.UNKNOWN_SHAPE)


def test_an_unreadable_list_keeps_its_hud_and_a_list_it_cannot_follow_counts_as_drawing():
    ram, at, size, _expected, _span = hud_list()
    assert hud.cut(hud.ram_words(ram), len(ram) - 8, size).reason == hud.UNREADABLE
    assert hud.cut(hud.closure_words(RUNS[1:], _closure(ram)[0x400:]), at, size).reason == hud.UNREADABLE

    def unreachable(rows):
        at = next(at for at, row in enumerate(rows) if row[1] == 0x80000000 | EXPANSION + 0x80)
        rows[at] = (*_call(0x80700000), True)              # outside the image: it cannot be shown to set state only
    ram, at, size, expected, span = hud_list(change=unreachable)
    assert hud.cut(hud.ram_words(ram), at, size) == hud.Cut(span, tuple(expected), None)


# ---------------------------------------------------------------- through the routes


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    """A tap session of three shown tasks whose master list is hud_list()'s, packaged."""
    base = tmp_path_factory.mktemp("scene-lab-hud")
    rom, _segment7 = synthetic._rom()
    ram, at, size, _expected, _span = hud_list()
    session = base / "tap-hud"
    session.mkdir()
    records, sequence = [], 0
    for frame in range(3):
        image = bytearray(ram)
        image[synthetic.TIMER:synthetic.TIMER + 4] = struct.pack(">I", 1000 + frame)
        image = pk.swap_words(bytes(image))
        fields = (0, 3, 1000, 2000, 0, len(RUNS), 1, sum(end - begin for begin, end in RUNS),
                  0x100000 + (frame % 2) * 0x80000, 1, frame, 0)
        task = (1, 0, 0x80246000, 208, 0x80247000, 4096, 0x80248000, 2048, 0x200000, 1024,
                0x210000, 0x220000, at, size, 0, 0)
        body = (struct.pack("<12I", *fields) + struct.pack("<16I", *task)
                + b"".join(struct.pack("<II", *run) for run in RUNS) + struct.pack("<II", synthetic.TIMER, 4)
                + b"".join(image[begin:end] for begin, end in RUNS) + image[synthetic.TIMER:synthetic.TIMER + 4])
        records.append(synthetic._tap_record(pk.tap.REC_TASK, sequence, body))
        records.append(synthetic._tap_record(pk.tap.REC_PRESENT, sequence + 1, synthetic._present_body(frame)))
        sequence += 2
    (session / "tap.bin").write_bytes(b"".join(records))
    (session / "session.txt").write_text(
        f"schema=scene-lab-tap/1\nwrapper_build=test\nqpc_frequency=10000000\nrdram_size={synthetic.RDRAM}\n"
        f"rom_header={pk.rom_header_hex(rom)}\n", encoding="ascii")
    (session / "armed.txt").write_text(
        f"extra={synthetic.TIMER:x}:4\n# identity=global_timer:{synthetic.TIMER:x}:4\n", encoding="ascii")
    package = base / "hud.sm64scene"
    pk.write_package(pk.load_source(session), rom, package, pk.CODECS["zlib"], group=2)
    return {"rom": rom, "package": package.read_bytes(), "ram": ram}


@pytest.fixture
def lab(tmp_path, recording, monkeypatch):
    monkeypatch.setattr(gpu_gate, "HOLD", tmp_path / "no-hold.txt")
    (tmp_path / "packages").mkdir()
    (tmp_path / "packages" / "hud.sm64scene").write_bytes(recording["package"])
    (tmp_path / "rom.z64").write_bytes(recording["rom"])
    (tmp_path / "Plugin").mkdir()
    path = tmp_path / "lab.json"
    path.write_text(json.dumps({"rom": str(tmp_path / "rom.z64"), "packages": str(tmp_path / "packages"),
                                "pj64_plugin": str(tmp_path / "Plugin")}), encoding="utf-8")
    return path


class HudViews(fake_viewer_host.FakeEvaluator):
    """Every free camera's view is hud_list()'s RDRAM (a fresh copy), as the evaluator splices
    the recorded HUD after its new world."""

    def __init__(self, ram):
        super().__init__()
        self.ram, self.size = ram, hud_list()[2]

    def display_list(self, index, camera):
        self.calls.append((index, camera))
        return SimpleNamespace(task=(1,) + (0,) * 15, ram=bytearray(self.ram), data_ptr=0x80000000 | LIST,
                               data_size=self.size)


def _app(lab, recording, ram_host: bool):
    app = FastAPI()
    host = scene_session.command_host(fake_viewer_host.command(ram=ram_host))
    scene_lab_api.mount_scene_lab(app, lab, session_options={
        "host_factory": host, "evaluator_factory": lambda package, rom: HudViews(recording["ram"])},
        watch_seconds=0.05)
    return app


def _reply(socket) -> dict:
    data = socket.receive()
    if data.get("bytes") is None:
        return json.loads(data["text"])
    length = struct.unpack_from("<I", data["bytes"])[0]
    return {**json.loads(data["bytes"][4:4 + length]), "payload": data["bytes"][4 + length:]}


def _pixels(digest: bytes, size: int = 64 * 48 * 3) -> bytes:
    return (digest * (size // 32 + 1))[:size]


def test_the_socket_draws_the_recorded_picture_without_the_hud_when_asked(lab, recording):
    """The host gets the same closure runs with the HUD's draws as no-ops (the fake host's
    picture names everything a request carried); the default and true keep them."""
    app = _app(lab, recording, ram_host=False)
    package = scene_session.scene_package.Package(recording["package"], recording["rom"])
    capture = package.frame(1)
    runs = struct.pack(f"<{2 * len(capture.runs)}I", *(edge for run in capture.runs for edge in run))
    cut = hud.cut(hud.closure_words(capture.runs, capture.run_bytes), capture.task[12], capture.task[13])
    assert cut.blanked == tuple(hud_list()[3])
    head = struct.pack("<16I14I", *capture.task, *capture.vi)
    with TestClient(app) as client:
        session = client.post("/api/scene-lab/open", json={"package": "hud.sm64scene"}).json()["session"]
        with client.websocket_connect("/api/scene-lab/ws") as socket:
            replies = []
            for ident, extra in enumerate(({}, {"hud": False}, {"hud": True})):
                socket.send_json({"id": ident, "session": session, "frame": 1, "camera": None, "encoding": "raw",
                                  **extra})
                replies.append(_reply(socket))
            socket.send_json({"id": 9, "session": session, "frame": 1, "camera": None, "hud": "no"})
            assert _reply(socket) == {"id": 9, "error": "hud is true or false"}
    with_hud, without, again = replies
    assert [reply["hud"] for reply in replies] == [True, False, True]
    assert {reply["hud_reason"] for reply in replies} == {None} and without["timings"]["hud_ms"] >= 0
    assert with_hud["payload"] == again["payload"] == _pixels(
        hashlib.sha256(head + runs + capture.run_bytes).digest())
    assert without["payload"] == _pixels(hashlib.sha256(
        head + runs + hud.blank_closure(capture.runs, capture.run_bytes, cut.blanked)).digest())


def test_a_free_camera_draws_its_view_without_the_hud_and_keeps_the_view_it_cached(lab, recording):
    """The host's RDRAM after a free picture without the HUD is the view with the HUD's draws as
    no-ops; the same camera with the HUD again brings them back (the view the session cached for
    Set focus was copied, not changed)."""
    app = _app(lab, recording, ram_host=True)
    ram = recording["ram"]
    blanked = hud.blank_ram(ram, hud_list()[3])
    camera = {"focus": [1.0, 2.0, 3.0], "position": [100.0, 200.0, 300.0]}
    with TestClient(app) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "hud.sm64scene"}).json()
        with client.websocket_connect("/api/scene-lab/ws") as socket:
            replies = []
            for ident, flag in enumerate((True, False, True, False)):
                socket.send_json({"id": ident, "session": opened["session"], "frame": 2, "camera": camera,
                                  "encoding": "raw", "hud": flag})
                replies.append(_reply(socket))
    assert [(reply["camera"], reply["hud"]) for reply in replies] == [
        ("free", True), ("free", False), ("free", True), ("free", False)]
    task = (1,) + (0,) * 11 + (LIST, hud_list()[2], 0, 0)
    vi = scene_session.scene_package.Package(recording["package"], recording["rom"]).frame(2).vi
    rest = bytes(fake_viewer_host.RDRAM_BYTES - len(ram))

    def picture(image):
        return _pixels(hashlib.sha256(struct.pack("<16I14I", *task, *vi) + pk.swap_words(bytes(image)) + rest).digest())
    assert replies[0]["payload"] == replies[2]["payload"] == picture(ram)
    assert replies[1]["payload"] == replies[3]["payload"] == picture(blanked) != picture(ram)
    assert 0 < replies[2]["timings"]["send_bytes"] < 4096                  # only the HUD's words went back
