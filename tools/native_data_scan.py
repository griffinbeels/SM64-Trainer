"""Refuse a built native that carries Nintendo's model data.

    uv run python tools/native_data_scan.py FILE...    # exit 1 naming each hit

The ghost's model draws with Nintendo's vertices, display lists, lights and
animations, but no native may ship them: renderer/model fills them from the
player's own ROM at open (renderer/model/rom_data.h). This scan holds every
built native to that. Its patterns come from the decomp sources the model
build pins (renderer/model/inputs.json, hash-checked in the build cache), so
no Nintendo bytes live in this repository either:

- each vertex array's first two vertices, as libsm64 compiles them (float
  positions, 24-byte stride), as 16-byte little-endian vertices, and as the
  ROM's big-endian ones;
- each animation value table's first 16 values, little- and big-endian;
- each display list's first four triangles, as libsm64's GFXCMD words and as
  the ROM's F3D G_TRI1 commands.

On 2026-11-09 the audit (.tasks/features/ghost-lab-beta/ip-audit.md, C1)
found Mario's vertices and the Bob-omb's walk in beta.37's renderer by two of
these patterns; tools/build_natives.py now runs this scan on every native it
compiles. Limits: a pattern is a sample (the first entries of each array),
geo layouts and lights carry no distinctive bytes and are not searched, and a
transformed copy (compressed, re-encoded) would pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
MODEL_INPUTS = ROOT / "renderer/model/inputs.json"
GFXCMD_TRIANGLE = 4  # gfx_adapter_commands.h: None, Material, GeometryMode, VertexData, Triangle

_VERTEX = re.compile(r"\{\{\{\s*(-?\d+),\s*(-?\d+),\s*(-?\d+)\},\s*(-?\w+),\s*\{\s*(-?\d+),\s*(-?\d+)\},"
                     r"\s*\{(\w+),\s*(\w+),\s*(\w+),\s*(\w+)\}\}\}")
_ARRAY = re.compile(r"^(?:static )?const (Vtx|s16|Gfx) (\w+)\[\] = \{(.*?)^\};", re.M | re.S)
_TRIANGLES = re.compile(r"gsSP(2Triangles|1Triangle)\(([^)]*)\)")


def default_cache() -> Path:
    import os
    import tempfile
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "SM64Trainer" / "build-cache" / "model"


def _sources(cache: Path) -> list[str]:
    """The pinned decomp C inputs, each checked against its pinned hash (and
    fetched into the cache, as the model build does, when missing)."""
    import importlib.util
    spec_ = importlib.util.spec_from_file_location("native_data_scan_build_model", ROOT / "tools/build_model.py")
    build_model = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(build_model)
    cache.mkdir(parents=True, exist_ok=True)
    texts = []
    for key, spec in json.loads(MODEL_INPUTS.read_text(encoding="utf-8")).items():
        if key != "libsm64":
            path = build_model._fetch(cache, spec, None)
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != spec["sha256"]:
                raise ValueError(f"model input hash mismatch: {path}")
            texts.append(data.decode("utf-8"))
    return texts


def _diverse(data: bytes, least: int) -> bool:
    return len(set(data)) >= least


def patterns_from(texts: list[str]) -> list[tuple[str, bytes]]:
    """(label, bytes) for every searched sample of the given decomp sources."""
    found: dict[bytes, str] = {}
    for text in texts:
        for kind, name, body in _ARRAY.findall(text):
            if kind == "Vtx":
                vertices = [tuple(int(v, 0) for v in match) for match in _VERTEX.findall(body)[:2]]
                if len(vertices) < 2:
                    continue
                flt = [struct.pack("<3fH2h4B", *map(float, v[:3]), *v[3:]) for v in vertices]
                samples = (flt[0] + b"\0\0" + flt[1],
                           b"".join(struct.pack("<3hH2h4B", *v) for v in vertices),
                           b"".join(struct.pack(">3hH2h4B", *v) for v in vertices))
                for form, sample in zip(("float", "le16", "rom"), samples, strict=True):
                    if _diverse(sample, 6):
                        found.setdefault(sample, f"{name} vertices ({form})")
            elif kind == "s16":
                values = [int(v, 0) for v in re.findall(r"0x[0-9A-Fa-f]+|-?\b\d+\b", body)[:16]]
                if len(values) < 16 or len(set(values)) < 8:
                    continue
                signed = [v - 0x10000 if v >= 0x8000 else v for v in values]
                found.setdefault(struct.pack("<16h", *signed), f"{name} animation values")
                found.setdefault(struct.pack(">16h", *signed), f"{name} animation values (rom)")
            else:
                triangles = []
                for macro, args in _TRIANGLES.findall(body):
                    numbers = [int(a, 0) for a in args.split(",")]
                    triangles += [numbers[0:4], numbers[4:8]] if macro == "2Triangles" else [numbers[0:4]]
                    if len(triangles) >= 4:
                        break
                if len(triangles) < 4:
                    continue
                triangles = triangles[:4]
                words = b"".join(struct.pack("<5I", GFXCMD_TRIANGLE, a, b, c, flag) for a, b, c, flag in triangles)
                rom = b"".join(struct.pack(">IBBBB", 0xBF000000, flag, a * 10, b * 10, c * 10)
                               for a, b, c, flag in triangles)
                if len({tuple(t[:3]) for t in triangles}) == 4:
                    found.setdefault(words, f"{name} triangles")
                    found.setdefault(rom, f"{name} triangles (rom)")
    return [(label, sample) for sample, label in found.items()]


def patterns(cache: Path | None = None) -> list[tuple[str, bytes]]:
    return patterns_from(_sources(cache or default_cache()))


def scan_bytes(data: bytes, searched: list[tuple[str, bytes]]) -> list[tuple[str, int]]:
    """Every (label, offset) at which a pattern occurs in data."""
    hits = []
    for label, sample in searched:
        at = data.find(sample)
        if at >= 0:
            hits.append((label, at))
    return hits


def require_clean(paths, searched: list[tuple[str, bytes]] | None = None) -> None:
    """Raise naming each file that holds model data."""
    searched = patterns() if searched is None else searched
    if not searched:
        raise ValueError("no model data patterns: the scan would pass anything")
    problems = []
    for path in map(Path, paths):
        hits = scan_bytes(path.read_bytes(), searched)
        if hits:
            shown = ", ".join(f"{label} at {offset:#x}" for label, offset in hits[:5])
            problems.append(f"{path.name} holds Nintendo model data ({len(hits)} samples: {shown})")
    if problems:
        raise RuntimeError("; ".join(problems))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--cache", type=Path, help="the model build cache holding the pinned decomp inputs")
    args = parser.parse_args()
    searched = patterns(args.cache)
    status = 0
    for path in args.files:
        hits = scan_bytes(path.read_bytes(), searched)
        print(f"{path}: {len(hits)} of {len(searched)} samples found"
              + "".join(f"\n  {label} at {offset:#x}" for label, offset in hits[:20]))
        status |= bool(hits)
    return status


if __name__ == "__main__":
    sys.exit(main())
