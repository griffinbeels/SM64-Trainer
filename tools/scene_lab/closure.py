"""Build and run the E0 closure probe over frame inputs (see savestate_frames.py).

    uv run python tools/scene_lab/closure.py <frame_dir>... [--iterations N]

Prints, per frame, the walker's inventory and its warm-cache walk/copy cost,
and writes closure.json beside each frame for replay.py poison.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402

SOURCES = (ROOT / "tools/scene_lab/closure_probe.cpp", ROOT / "plugin/gfxwrap/scene_closure.h")


def build() -> Path:
    spec = importlib.util.spec_from_file_location("scene_lab_build", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    digest = hashlib.sha256(b"".join(path.read_bytes() for path in SOURCES)).hexdigest()[:16]
    work = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "SM64Trainer/build-cache/scene-lab" / digest
    target = work / "closure_probe.exe"
    if target.is_file():
        return target
    work.mkdir(parents=True, exist_ok=True)
    vcvars = compiler.find_vcvars32()
    if vcvars is None:
        raise RuntimeError("x86 MSVC toolchain is required")
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17",
                          f"/I{ROOT / 'plugin/gfxwrap'}", str(SOURCES[0]), f"/Fe:{target}",
                          f"/Fo{work}\\"], work)
    return target


def probe(executable: Path, frame: Path, iterations: int) -> dict:
    result = subprocess.run([str(executable), str(frame), str(frame / "closure.json"), str(iterations)],
                            capture_output=True, text=True, timeout=120, check=False,
                            **quiet_spawn_kwargs())
    if result.returncode not in (0, 1) or not result.stdout.strip():
        raise RuntimeError(f"closure_probe failed ({result.returncode}): {result.stderr.strip()}")
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("frames", type=Path, nargs="+")
    parser.add_argument("--iterations", type=int, default=200)
    args = parser.parse_args()
    executable = build()
    for frame in args.frames:
        report = probe(executable, frame, args.iterations)
        print(frame.name, json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
