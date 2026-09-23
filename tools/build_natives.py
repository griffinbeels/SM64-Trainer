"""Make this checkout's three bundled natives match its sources.

    uv run python tools/build_natives.py            # build what is out of date
    uv run python tools/build_natives.py --strict   # and fail if any cannot be (wrap, release)
    uv run python tools/build_natives.py --check    # only say what is out of date

The capture wrapper, the NVENC encoder helper and the renderer are BUILD
OUTPUTS: git ignores src/sm64_events/data/plugin/ (since 2026-09-23; before
that every native edit committed a 12.5 MB renderer, and parallel branches
conflicted on it). Each file carries an embedded build id that names its
sources (tools/build_plugin.py, tools/build_renderer.py). A file whose id
matches is left alone; otherwise the build for that id comes from a
content-keyed cache shared by every checkout
(%LOCALAPPDATA%/SM64Trainer/build-cache/natives/<id>), and only a cache miss
compiles: about 20 s for the renderer, 3-7 s for each of the other two.

Who runs it: `python -m sm64_events.main` from a checkout (so
run-test-server.bat) before serving, the full verification lane (so wrap),
and tools/release.py before packaging. A native that cannot be built (no
MSVC toolchain, or the renderer's prebuilt libraries missing from the build
cache) is REMOVED from the bundle rather than left stale: the setup screen
then reports the capture layer as unavailable instead of offering to
install code built from other sources (his report, 2026-09-15).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Callable, NamedTuple

REPO = Path(__file__).resolve().parents[1]
BUNDLE = REPO / "src" / "sm64_events" / "data" / "plugin"
KEEP_PER_NATIVE = 6  # cached builds kept per file: a few branches' worth


def cache_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "SM64Trainer" / "build-cache" / "natives"


def _load(name: str):
    module = sys.modules.get(f"natives_{name}")
    if module is None:
        spec = importlib.util.spec_from_file_location(f"natives_{name}", REPO / "tools" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


def embedded_id(path: Path) -> str | None:
    """The build id compiled into a native (core/capturelayer.py reads it the same way)."""
    from sm64_events.core.capturelayer import _build_identity
    try:
        found = _build_identity(path)
    except OSError:
        return None
    return found.decode("ascii") if found else None


class Native(NamedTuple):
    name: str
    identity: Callable[[], str]
    build: Callable[[Path], Path]
    needs: str  # what building it takes, said when it cannot be built


def natives() -> tuple[Native, ...]:
    plugin, renderer = _load("build_plugin"), _load("build_renderer")
    return (
        Native("sm64_trainer_gfx.dll", lambda: plugin.wrapper_build_id() + "-gpu-runtime",
               plugin.build_wrapper, "the x86 MSVC toolchain (VS Build Tools)"),
        Native("SM64GpuEncoderV1.dll", plugin.helper_build_id, plugin.build_encoder_helper,
               "the x64 MSVC toolchain (VS Build Tools)"),
        Native("GLideN64_SM64Trainer.dll", renderer.renderer_build_id, renderer.build_renderer,
               "the MSVC toolchain and the renderer's prebuilt libraries (renderer/README.md)"),
    )


def _entry(native: Native, expected: str) -> Path:
    return cache_root() / native.name / hashlib.sha256(expected.encode()).hexdigest()[:32]


def _cached_build(native: Native, expected: str, log) -> Path:
    """The cached file for this id, compiling it on a miss."""
    entry = _entry(native, expected)
    cached = entry / native.name
    if cached.is_file() and embedded_id(cached) == expected:
        os.utime(entry)
        return cached
    entry.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="building-", dir=entry.parent))
    try:
        started = time.monotonic()
        log(f"  building {native.name} (not built from these sources yet)...")
        built = native.build(staging)
        if embedded_id(built) != expected:
            raise RuntimeError(f"{native.name} built without the expected id {expected}")
        log(f"  built {native.name} in {time.monotonic() - started:.0f} s")
        shutil.rmtree(entry, ignore_errors=True)
        try:
            os.replace(staging, entry)
        except OSError:  # another checkout stored the same build first
            pass
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    _prune(entry.parent)
    return cached


def _prune(folder: Path) -> None:
    entries = sorted((path for path in folder.iterdir() if path.is_dir() and not path.name.startswith("building-")),
                     key=lambda path: path.stat().st_mtime, reverse=True)
    for stale in entries[KEEP_PER_NATIVE:]:
        shutil.rmtree(stale, ignore_errors=True)


def _install(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    shutil.copyfile(source, partial)
    os.replace(partial, target)


def ensure(*, build: bool = True, strict: bool = False, log=print) -> dict[str, str]:
    """Each native's state after the run: fresh, built, stale or missing.

    With `build`, an out-of-date native is taken from the cache or compiled;
    one that cannot be is removed from the bundle. `strict` raises instead of
    returning when any native is not fresh afterwards."""
    states, problems = {}, []
    for native in natives():
        target = BUNDLE / native.name
        expected = native.identity()
        if target.is_file() and embedded_id(target) == expected:
            states[native.name] = "fresh"
            continue
        if not build:
            states[native.name] = "stale" if target.is_file() else "missing"
            problems.append(f"{native.name} is {states[native.name]}")
            continue
        try:
            _install(_cached_build(native, expected, log), target)
            states[native.name] = "built"
        except PermissionError as failure:
            states[native.name] = "stale"
            problems.append(f"{native.name} is in use and was not replaced ({failure})")
        except Exception as failure:  # noqa: BLE001 - any build failure reads the same to him
            target.unlink(missing_ok=True)
            states[native.name] = "missing"
            problems.append(f"{native.name} could not be built: it needs {native.needs}. "
                            f"({str(failure).splitlines()[0] if str(failure) else type(failure).__name__})")
    for problem in problems:
        log(f"  WARNING: {problem}")
    if strict and problems:
        raise RuntimeError("; ".join(problems))
    return states


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report only; exit 1 if any is out of date")
    mode.add_argument("--strict", action="store_true", help="exit 1 if any cannot be built")
    args = parser.parse_args()
    sys.path.insert(0, str(REPO / "src"))
    try:
        states = ensure(build=not args.check, strict=args.strict)
    except RuntimeError as failure:
        print(f"natives: FAILED: {failure}")
        return 1
    print("natives: " + ", ".join(f"{name} {state}" for name, state in states.items()))
    return 1 if args.check and any(state != "fresh" for state in states.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
