"""Scan our built natives with Microsoft Defender before they are installed or shipped.

    uv run python tools/defender_scan.py FILE...    # exit 0 clean (or no scanner), 2 threat, 1 scan failed

On 2026-11-06 Defender quarantined our own renderer as
"Trojan:Win32/Bearfoos.A!ml", a cloud machine-learning verdict on one build,
in three places at once, and replays lost their renderer. This gate finds
such a verdict on the build machine first, and names the file.

Each file gets `MpCmdRun -Scan -ScanType 3 -File <file> -DisableRemediation`:
a custom scan of that one file that reports without quarantining or deleting
it. Exit 0 means no threat, 2 a threat; anything else is a scan that did not
finish. It needs no administrator rights. Real-time protection may still act
on a flagged file by itself; this gate never changes Defender's settings,
exclusions or quarantine. Off Windows, or without MpCmdRun, it says so and
the caller continues: it is a safety net, not a substitute for the
behaviours the design record fixes.

Who runs it: tools/build_natives.py after it builds or installs anything
(a threat stops it and removes the file from the bundle), and the release and
beta builders (tools/build_exe.py, tools/build_beta.py), which refuse a
flagged file and, when strict, a scan that could not finish.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, NamedTuple

CLEAN, THREAT, FAILED, UNAVAILABLE = "clean", "threat", "failed", "unavailable"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Verdict(NamedTuple):
    path: Path
    state: str   # clean, threat, failed or unavailable
    detail: str  # the threat's own lines, the failure, or why there was no scan


class ThreatFound(RuntimeError):
    """Defender reported a threat in one of our files."""

    def __init__(self, verdicts: list[Verdict]):
        self.verdicts = verdicts
        super().__init__("; ".join(f"Microsoft Defender reports a threat in {verdict.path}: {verdict.detail}"
                                   for verdict in verdicts))


def find_scanner() -> Path | None:
    """MpCmdRun.exe of the current Defender platform, else the inbox copy."""
    if os.name != "nt":
        return None
    platform = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft" / "Windows Defender" / "Platform"
    candidates = [platform / "CurrentVersion" / "MpCmdRun.exe"]
    if platform.is_dir():
        def version(folder: Path) -> tuple:
            return tuple(int(part) for part in folder.name.split("-")[0].split(".") if part.isdigit())
        candidates += [folder / "MpCmdRun.exe" for folder in sorted(
            (entry for entry in platform.iterdir() if entry.is_dir() and entry.name[:1].isdigit()),
            key=version, reverse=True)]
    candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Windows Defender" / "MpCmdRun.exe")
    return next((path for path in candidates if path.is_file()), None)


def _run(command: list[str]) -> tuple[int, str]:
    result = subprocess.run(command, capture_output=True, text=True, errors="replace", check=False,
                            creationflags=_NO_WINDOW, stdin=subprocess.DEVNULL, timeout=600)
    return result.returncode, result.stdout + result.stderr


Runner = Callable[[list[str]], tuple[int, str]]


def scan(paths, *, runner: Runner | None = None, scanner: Path | None = None) -> list[Verdict]:
    """One verdict per file. `runner` and `scanner` stand in for MpCmdRun in tests."""
    paths = [Path(path) for path in paths]
    if runner is None:
        scanner = scanner or find_scanner()
        if scanner is None:
            why = "not Windows" if os.name != "nt" else "Microsoft Defender's MpCmdRun.exe was not found"
            return [Verdict(path, UNAVAILABLE, why) for path in paths]
        runner = _run
    verdicts = []
    for path in paths:
        if not path.is_file():
            # Real-time protection can quarantine a file between the build and this scan.
            verdicts.append(Verdict(path, FAILED, "the file is gone (real-time protection may have removed it)"))
            continue
        code, output = runner([str(scanner or "MpCmdRun.exe"), "-Scan", "-ScanType", "3",
                               "-File", str(path.resolve()), "-DisableRemediation"])
        if code == 0:
            verdicts.append(Verdict(path, CLEAN, "no threat"))
        elif code == 2:
            lines = [line.strip() for line in output.splitlines() if "threat" in line.lower() and line.strip()]
            verdicts.append(Verdict(path, THREAT, " | ".join(lines[:4]) or "threat found (exit 2)"))
        else:
            tail = " | ".join(line.strip() for line in output.splitlines()[-3:] if line.strip())
            verdicts.append(Verdict(path, FAILED, f"scan did not finish (exit {code}): {tail}"))
    return verdicts


def require_clean(paths, *, strict: bool = False, runner: Runner | None = None, scanner: Path | None = None,
                  log=print) -> list[Verdict]:
    """Scan and report; raise ThreatFound on any threat. `strict` (release and
    beta builds) also refuses a scan that did not finish. No scanner is
    reported and passes: there is nothing to run."""
    verdicts = scan(paths, runner=runner, scanner=scanner)
    for verdict in verdicts:
        if verdict.state == CLEAN:
            log(f"  Defender: {verdict.path.name} clean")
        elif verdict.state == UNAVAILABLE:
            log(f"  Defender: {verdict.path.name} not scanned: {verdict.detail}")
        elif verdict.state == FAILED:
            log(f"  WARNING: Defender could not scan {verdict.path}: {verdict.detail}")
        else:
            log(f"  STOP: Microsoft Defender reports a threat in {verdict.path}: {verdict.detail}")
    threats = [verdict for verdict in verdicts if verdict.state == THREAT]
    if threats:
        raise ThreatFound(threats)
    failed = [verdict for verdict in verdicts if verdict.state == FAILED]
    if strict and failed:
        raise RuntimeError("; ".join(f"Defender could not scan {verdict.path}: {verdict.detail}" for verdict in failed))
    return verdicts


def main(argv: list[str] | None = None) -> int:
    paths = (sys.argv[1:] if argv is None else argv)
    if not paths:
        print(__doc__.split("\n\n")[0])
        return 1
    try:
        verdicts = require_clean(paths)
    except ThreatFound:
        return 2
    return 1 if any(verdict.state == FAILED for verdict in verdicts) else 0


if __name__ == "__main__":
    sys.exit(main())
