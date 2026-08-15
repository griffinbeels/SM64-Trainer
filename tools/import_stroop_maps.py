"""Turn STROOP's linker maps into the package data memory/behaviours.py reads.

    uv run python tools/import_stroop_maps.py <MappingUS.map> <MappingJP.map>

Held VERBATIM: each TSV row is the map's row byte for byte (address, symbol),
so a disagreement with the source is checkable by diffing, never by
re-deriving. Two files per version:

  behaviours_<v>.tsv  every symbol the linker placed in segment 0x13 -- the
                      behaviour scripts; the SYMBOL is the identity the
                      tracker keys landmarks by, the offset is per version
  symbols_<v>.tsv     the RAM globals memory/layout.py derives from a symbol
                      (LAYOUT_ROWS with a `symbol`), so a fresh version has a
                      CANDIDATE address before anyone plays

Source (fetched 2026-08-15): github.com/SM64-TAS-ABC/STROOP @ dev,
STROOP/Mappings/MappingUS.map and MappingJP.map.
"""
import re
import sys
from pathlib import Path

from sm64_events.memory.layout import LAYOUT_ROWS

OUT = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "data"
ROW = re.compile(r"^\s*0x([0-9a-f]{16})\s+(\S+)\s*$")
WANTED_GLOBALS = frozenset(row.symbol for row in LAYOUT_ROWS if row.symbol)
BEHAVIOUR_SEGMENT = range(0x13000000, 0x14000000)


def parse(path: Path) -> list[tuple[int, str]]:
    rows = []
    for line in path.read_bytes().decode("utf-8", errors="replace").splitlines():
        match = ROW.match(line)
        if match:
            rows.append((int(match.group(1), 16), match.group(2)))
    return rows


def write(path: Path, rows, source: str) -> None:
    lines = [f"# STROOP {source} @ dev, fetched 2026-08-15"]
    lines += [f"{addr:#010x}\t{sym}" for addr, sym in sorted(rows)]
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def main(us: str, jp: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for version, path in (("us", Path(us)), ("jp", Path(jp))):
        rows = parse(path)
        behaviours = [(a, s) for a, s in rows if a in BEHAVIOUR_SEGMENT]
        globals_ = [(a, s) for a, s in rows if s in WANTED_GLOBALS]
        write(OUT / f"behaviours_{version}.tsv", behaviours, path.name)
        write(OUT / f"symbols_{version}.tsv", globals_, path.name)
        print(f"{version}: {len(behaviours)} behaviours, {len(globals_)} globals")


if __name__ == "__main__":
    main(*sys.argv[1:3])
