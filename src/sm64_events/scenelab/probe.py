# SPDX-License-Identifier: GPL-2.0-or-later
# Runs in the simulation program (SM64TrainerSim.exe); licenses/README.md lists its GPL-2.0-or-later code.
"""The installed app's Ghost Lab self-checks: what a frozen build proves before anyone plays with it.

Three receipts, each a JSON file of {"ok", "checks": [{"name", "ok", ...}]}:

    sim_self_check      SM64TrainerSim.exe --sim-self-check RECEIPT. Inside the simulation program: every module a
                        simulation process may load imports (sim_boundary.MODULES), Unicorn runs one MIPS
                        instruction, hashing works without OpenSSL, PyAV and pystray are refused, the bundled maps
                        are the reviewed bytes, every Ghost Lab native is found from this program, and the CPU
                        hooks DLL loads.
    trainer_lab_checks  part of SM64Trainer.exe --beta-self-check (desktop/beta_probe.py): every Ghost Lab module the
                        Trainer carries imports (scenelab_code.LAB_MODULES), the Trainer holds no Unicorn, and the
                        sibling simulation program passes its own check.
    lab_self_check      SM64Trainer.exe --lab-self-check RECEIPT --rom ROM [--clip CLIP] [--work DIR]: the lab's
                        work on the CPU through the real child processes, as the server starts them: a stage made
                        from the cartridge alone (cold boot, practice reset, a stage scene packed by
                        tap_offline_host.exe), the same stage again from the cache, a stage warmed ahead twice
                        (the first warm and a cached run), and with a ghost clip one re-simulated ghost. Each step
                        records its seconds. The ROM is read, never written; everything it makes goes under the work
                        folder (a new temporary folder unless named).

None of them draws a picture (no GPU), starts the server or the window, or touches a profile's data.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

SIM_FLAG = "--sim-self-check"
LAB_FLAG = "--lab-self-check"
SIM_SECONDS = 180.0
STEP_SECONDS = 600.0
TRUNCATE = 600


class Receipt:
    def __init__(self, kind: str):
        self.data = {"schema": 1, "kind": kind, "ok": False, "checks": []}

    def check(self, name: str, work, **extra) -> bool:
        began = time.perf_counter()
        item = {"name": name, **extra}
        try:
            value = work()
            item["ok"] = bool(value) if not isinstance(value, dict) else bool(value.get("ok"))
            if isinstance(value, dict):
                item.update({key: val for key, val in value.items() if key != "ok"})
        except BaseException as error:  # noqa: BLE001 - a maintainer receipt: the failure is the finding
            item["ok"] = False
            item["error"] = f"{type(error).__name__}: {error}"[:TRUNCATE]
            item["trace"] = traceback.format_exc()[-TRUNCATE:]
        item["seconds"] = round(time.perf_counter() - began, 3)
        self.data["checks"].append(item)
        return item["ok"]

    def finish(self) -> dict:
        self.data["ok"] = bool(self.data["checks"]) and all(item["ok"] for item in self.data["checks"])
        return self.data


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _receipt_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path.suffix.lower() != ".json" or not path.parent.is_dir():
        raise ValueError("the receipt is an absolute .json path in a folder that exists")
    return path


# ---------------------------------------------------------------- inside the simulation program

def _unicorn_runs_mips() -> bool:
    from unicorn import UC_ARCH_MIPS, UC_MODE_BIG_ENDIAN, UC_MODE_MIPS64, Uc
    from unicorn.mips_const import UC_MIPS_REG_V0
    engine = Uc(UC_ARCH_MIPS, UC_MODE_MIPS64 | UC_MODE_BIG_ENDIAN)   # what frame_sim and e2_georender build
    engine.mem_map(0x1000, 0x1000)
    engine.mem_write(0x1000, bytes.fromhex("2402002a"))              # addiu $v0, $zero, 42
    engine.emu_start(0x1000, 0x1004)
    return engine.reg_read(UC_MIPS_REG_V0) == 42


def _refused(name: str) -> bool:
    try:
        importlib.import_module(name)
    except ImportError:          # sim_boundary.Refused, or the library is not in this program at all
        return True
    return False


def sim_self_check(argv: list[str]) -> int:
    """SM64TrainerSim.exe --sim-self-check RECEIPT: 0 when every check passes."""
    if len(argv) != 1:
        return 2
    target = _receipt_path(argv[0])
    receipt = Receipt("simulation-program-self-check")
    from sm64_events.scenelab_code import lab_module
    receipt.check("sim_boundary_enter", lambda: lab_module("sim_boundary").enter() is None)
    sim_boundary = lab_module("sim_boundary")
    for name in sim_boundary.MODULES:
        receipt.check("import:" + name, lambda name=name: importlib.import_module(name) is not None)
    receipt.check("unicorn_runs_mips", _unicorn_runs_mips)
    receipt.check("hashing_without_openssl", lambda: hashlib.sha256(b"abc").hexdigest().startswith("ba7816bf")
                  and importlib.util.find_spec("_hashlib") is None and importlib.util.find_spec("_ssl") is None)
    receipt.check("gpl3_libraries_refused", lambda: _refused("av") and _refused("pystray"))
    from sm64_events.core import paths
    from sm64_events.core.update_plan import LAB_NATIVES
    for name, pinned in paths.LAB_MAP_SHA256.items():
        receipt.check("map:" + name, lambda name=name, pinned=pinned: (found := paths.bundled_lab_file(name))
                      is not None and hashlib.sha256(found.read_bytes()).hexdigest() == pinned)
    for name in LAB_NATIVES:
        receipt.check("lab_native:" + name, lambda name=name: paths.bundled_lab_file(name) is not None)
    receipt.check("cpu_hooks_load", lambda: lab_module("cpu_hooks").library() is not None)
    receipt.check("no_trainer_only_libraries", lambda: not {"av", "pystray", "webview", "fastapi", "PIL"}
                  & {name.split(".")[0] for name in sys.modules})
    _write(target, receipt.finish())
    return 0 if receipt.data["ok"] else 2


# ---------------------------------------------------------------- inside the Trainer

def sim_program() -> Path:
    from sm64_events.scenelab_code import SIM_PROGRAM
    return Path(sys.executable).resolve().parent / SIM_PROGRAM


def run_sim_self_check(folder: Path | None = None) -> dict:
    """Starts the sibling simulation program's own check and returns its receipt."""
    import os
    from sm64_events.core.childproc import other_program_env, quiet_spawn_kwargs
    environment = other_program_env(os.environ.copy())
    with tempfile.TemporaryDirectory(prefix="sm64-sim-self-check-", dir=folder) as work:
        target = Path(work) / "sim.json"
        done = subprocess.run([str(sim_program()), SIM_FLAG, str(target)], stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=SIM_SECONDS, env=environment, check=False,
                              **quiet_spawn_kwargs())
        if not target.is_file():
            return {"ok": False, "returncode": done.returncode,
                    "stderr": done.stderr.decode("utf-8", "replace")[-TRUNCATE:]}
        return json.loads(target.read_text(encoding="utf-8")) | {"returncode": done.returncode}


def trainer_lab_checks(check) -> dict | None:
    """The Ghost Lab's rows of the beta self-check (`check(name, work)` is beta_probe's). Returns the simulation
    program's receipt, which the report carries whole."""
    from sm64_events.scenelab_code import LAB_MODULES, lab_module
    for name in LAB_MODULES:
        check("lab_import:" + name, lambda name=name: lab_module(name) is not None)
    check("trainer_holds_no_unicorn", lambda: importlib.util.find_spec("unicorn") is None)
    held: dict = {}

    def simulation():
        held.update(run_sim_self_check())
        return held.get("ok") is True
    check("simulation_program", simulation)
    return held or None


# ---------------------------------------------------------------- the lab's work, through its children

def _child(command: list[str], timeout: float = STEP_SECONDS) -> dict:
    """ghost_resim in its child process, as server/scene_resim.py starts it: its last JSON line."""
    from sm64_events.core.childproc import python_child, quiet_spawn_kwargs
    argv, env = python_child("ghost_resim", "--scene-lab-resim", command, site_packages=True)
    tools = Path(__file__).resolve().parents[3] / "tools" / "scene_lab"
    if tools.is_dir():                                   # a source checkout: the lab's own folder on the path
        env["PYTHONPATH"] = str(tools) + (";" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    done = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace",
                          env=env, timeout=timeout, check=False, **quiet_spawn_kwargs())
    lines = [line for line in done.stdout.strip().splitlines() if line.startswith("{") and '"progress"' not in line]
    answer = json.loads(lines[-1]) if lines else {}
    answer = {"answer": answer, "returncode": done.returncode}
    if done.returncode != 0 or "error" in answer["answer"] or not answer["answer"]:
        answer["stderr"] = done.stderr[-TRUNCATE:]
    return answer


def _map_for(rom: Path) -> Path:
    from sm64_events.core.paths import bundled_lab_file
    from sm64_events.scenelab.roms import identify, to_big_endian
    image = to_big_endian(rom.read_bytes())
    region = identify(image) if image is not None else None
    if region is None:
        raise ValueError("the ROM is neither Usamune v1.93u nor v1.93j")
    name = {"us": "MappingUS.map", "jp": "MappingJP.map"}[region]
    found = bundled_lab_file(name)
    if found is None:                                    # a source checkout: the committed maps
        found = Path(__file__).resolve().parents[1] / "data" / "maps" / name
    return found


def lab_self_check(argv: list[str]) -> int:
    """SM64Trainer.exe --lab-self-check RECEIPT --rom ROM [--clip CLIP] [--work DIR]: 0 when every step passes."""
    import argparse
    parser = argparse.ArgumentParser(prog=LAB_FLAG)
    parser.add_argument("receipt")
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--clip", type=Path)
    parser.add_argument("--work", type=Path)
    try:
        args = parser.parse_args(argv)
        target = _receipt_path(args.receipt)
    except (SystemExit, ValueError):
        return 2
    work = args.work or Path(tempfile.mkdtemp(prefix="sm64-lab-self-check-"))
    work.mkdir(parents=True, exist_ok=True)
    receipt = Receipt("ghost-lab-self-check")
    receipt.data.update(work=str(work), frozen=bool(getattr(sys, "frozen", False)))
    common: list[str] = []

    def inputs():
        nonlocal common
        rom = args.rom.resolve()
        common = ["--lab-root", str(work / "lab"), "--cache", str(work / "lab" / "resim"), "--rom", str(rom),
                  "--map", str(_map_for(rom))]
        (work / "lab").mkdir(parents=True, exist_ok=True)
        return {"ok": True, "map": common[-1]}
    if not receipt.check("inputs", inputs):
        _write(target, receipt.finish())
        return 2

    def step(command):
        def run():
            told = _child([*command[:1], *common, *command[1:]])
            answer = told["answer"]
            path = answer.get("path")
            return {**told, "ok": told["returncode"] == 0 and "error" not in answer
                    and (path is None or Path(path).is_file())}
        return run
    stage = ["stage", "--course", "1", "--area", "1", "--act", "1", "--frames", "60"]
    receipt.check("stage_cold", step(stage))                       # cold boot, practice reset, a packed scene
    receipt.check("stage_cached", step(stage))
    warm = ["warm", "--level", "24", "--area", "1", "--act", "2"]
    receipt.check("warm_first", step(warm))                        # what the warmer does for each stage
    receipt.check("warm_cached", step(warm))
    if args.clip is not None:
        receipt.check("ghost_resim", step(["build", "--clip", str(args.clip.resolve()),
                                            "--starts", str(work / "starts")]))
    _write(target, receipt.finish())
    return 0 if receipt.data["ok"] else 2
