"""Turn STROOP's linker maps into the package data memory/behaviours.py reads.

    uv run python tools/import_stroop_maps.py <MappingUS.map> <MappingJP.map>

Held ROW FOR ROW: each TSV row is the map's (address, symbol) pair -- the
address re-emitted as 0x%08x, the symbol untouched -- so a disagreement with
the source is checkable by diffing, never by re-deriving. Two files per version:

  behaviours_<v>.tsv  every symbol the linker placed in segment 0x13 -- the
                      behaviour scripts; the SYMBOL is the identity the
                      tracker keys landmarks by, the offset is per version
  symbols_<v>.tsv     the RAM globals memory/layout.py derives from a symbol
                      (LAYOUT_ROWS with a `symbol`), so a fresh version has a
                      CANDIDATE address before anyone plays

And one file for both: build_shifts.tsv, where game memory named by a symbol
moves between the builds (slotlibrary/translate.py). Every symbol both maps
name once, at or above 0x80000000, sorted by its address in one build; a row
starts each run of symbols that move by the same amount. An address takes the
shift of the last row at or below it. Rows are generated, not copied, so they
are checked by re-running this against the same maps.

Source (fetched 2026-08-15): github.com/SM64-TAS-ABC/STROOP @ dev,
STROOP/Mappings/MappingUS.map and MappingJP.map. build_shifts.tsv was made
from the same files at 6771197ad0f50718d3d4a13387e01d1e6d9c0d1a
(tools/scene_lab/cold_boot.py fetch-maps), 2026-11-08.
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


def shift_runs(source: dict[str, int], target: dict[str, int]) -> list[tuple[int, int, str]]:
    """(source address, target minus source, first symbol) for each run of shared symbols moving alike."""
    runs: list[tuple[int, int, str]] = []
    for address, name in sorted((address, name) for name, address in source.items() if name in target):
        delta = target[name] - address
        if not runs or runs[-1][1] != delta:
            runs.append((address, delta, name))
    return runs


def ram_symbols(rows: list[tuple[int, str]]) -> dict[str, int]:
    """Symbols named once in a map, in RAM. A name given twice cannot say which address moved where."""
    seen: dict[str, list[int]] = {}
    for address, symbol in rows:
        seen.setdefault(symbol, []).append(address)
    return {symbol: found[0] for symbol, found in seen.items() if len(found) == 1 and found[0] >= 0x80000000}


def write_shifts(path: Path, us_rows, jp_rows) -> int:
    us, jp = ram_symbols(us_rows), ram_symbols(jp_rows)
    lines = ["# STROOP MappingUS.map and MappingJP.map @ 6771197ad0f50718d3d4a13387e01d1e6d9c0d1a, 2026-11-08:",
             "# from\tstart\tshift\tfirst symbol of the run (tools/import_stroop_maps.py)"]
    count = 0
    for name, source, target in (("us", us, jp), ("jp", jp, us)):
        for address, delta, symbol in shift_runs(source, target):
            lines.append(f"{name}\t{address:#010x}\t{delta:+#x}\t{symbol}")
            count += 1
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return count


def main(us: str, jp: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    parsed = {}
    for version, path in (("us", Path(us)), ("jp", Path(jp))):
        rows = parsed[version] = parse(path)
        behaviours = [(a, s) for a, s in rows if a in BEHAVIOUR_SEGMENT]
        globals_ = [(a, s) for a, s in rows if s in WANTED_GLOBALS]
        write(OUT / f"behaviours_{version}.tsv", behaviours, path.name)
        write(OUT / f"symbols_{version}.tsv", globals_, path.name)
        print(f"{version}: {len(behaviours)} behaviours, {len(globals_)} globals")
    print(f"build shifts: {write_shifts(OUT / 'build_shifts.tsv', parsed['us'], parsed['jp'])} runs")


if __name__ == "__main__":
    main(*sys.argv[1:3])
