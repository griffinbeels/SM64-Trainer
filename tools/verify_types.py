"""Strict type-checking pilot for timefmt.py and the browser timecurve module."""
import argparse
import json
from pathlib import Path
import os
import sys

from verify_lint import ROOT, Unavailable, run

TOOLS = ROOT / "tools/verification/node_modules"
PYRIGHT = "1.1.405"
TYPESCRIPT = "5.9.3"


def commands(root: Path = ROOT) -> list[list[str]]:
    return [["node", str(TOOLS / "pyright/index.js"), "--project", str(root / "pyrightconfig.json")],
            ["node", str(TOOLS / "typescript/lib/tsc.js"), "--project", str(root / "jsconfig.verification.json")]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args(argv)
    try:
        for filename, key in (("pyrightconfig.json", "include"), ("jsconfig.verification.json", "files")):
            scope = json.loads((ROOT / filename).read_text(encoding="utf-8"))[key]
            if not scope or any(not (ROOT / path).is_file() for path in scope):
                raise Unavailable(f"{filename}: expected explicit source scope is missing")
        interpreter = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not interpreter.is_file():
            raise Unavailable("project Python environment missing; run uv sync --frozen")
        if args.probe:
            python = run([str(interpreter), "--version"])
            if python.returncode:
                raise Unavailable("project Python interpreter unavailable")
            print(python.stdout.strip())
        status = 0
        for command, expected in zip(commands(), [f"pyright {PYRIGHT}", f"Version {TYPESCRIPT}"], strict=True):
            result = run([*command[:2], "--version"] if args.probe else command)
            if args.probe and (result.returncode or result.stdout.strip() != expected):
                raise Unavailable(result.stderr.strip() or f"expected {expected}; got {result.stdout.strip()}")
            if result.returncode not in (0, 1, 2):
                raise Unavailable(result.stderr.strip() or "type checker crashed")
            if expected.startswith("pyright") and result.returncode == 2:
                raise Unavailable(result.stderr.strip() or result.stdout.strip() or "Pyright failed")
            if result.returncode and not result.stdout.strip():
                raise Unavailable(result.stderr.strip() or "type checker failed without diagnostics")
            print(result.stdout.strip() or "checkJs: no errors")
            if result.returncode:
                status = 1
        return status
    except (Unavailable, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"types: unavailable: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
