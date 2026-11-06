"""Sign our own release files with Microsoft Artifact Signing, or say plainly that they are unsigned.

    uv run python tools/code_signing.py --status    # is signing set up on this machine?
    uv run python tools/code_signing.py FILE...     # sign them (or report "unsigned")

Signing names a publisher on each file: Windows shows it in the file's
Properties and in SmartScreen's prompt, and Microsoft's developer guidance
says consistent signing "helps our research team quickly identify the source
of a program" (learn.microsoft.com/en-us/defender-xdr/developer-faq). Setup is
docs/code-signing.md: an Artifact Signing account (formerly Trusted Signing),
a validated identity and a certificate profile, all in Griffin's own Azure
subscription. Nothing about that account lives in this repository.

The configuration is a JSON file OUTSIDE the repository, named by the
environment variable SM64_SIGNING_CONFIG, else
%LOCALAPPDATA%/SM64Trainer/signing/signing.json:

    {"dlib": "C:/.../bin/x64/Azure.CodeSigning.Dlib.dll",
     "metadata": "C:/.../metadata.json",
     "signtool": "C:/.../x64/signtool.exe"}          optional; else the Windows SDK's

`metadata` is Microsoft's own file (Endpoint, CodeSigningAccountName,
CertificateProfileName). With no configuration every call returns an
unsigned result and says so; a configured signing that fails raises, so a
release never ships half signed. The command is the one Microsoft documents
for SignTool (learn.microsoft.com/en-us/azure/artifact-signing/how-to-signing-integrations),
without its /debug diagnostics:

    signtool sign /v /fd SHA256 /tr http://timestamp.acs.microsoft.com /td SHA256
                  /dlib <dlib> /dmdf <metadata> <files>

followed by `signtool verify /pa` of each file (the FAQ's check for a
"Command succeeded" that signed nothing). Artifact Signing certificates last
three days, so the timestamp is what keeps a signature valid afterwards.

Who calls it: tools/build_exe.py signs the packaged natives and the app's
SM64Trainer.exe after PyInstaller and before the zip and manifest are made
(release.py records those signed bytes); tools/build_beta.py does the same
for a legacy private beta. Installers that carry an appended payload
(the private beta installers) and the PyInstaller one-file bootstrap are not
signed here: signing appends a certificate table that their payload readers
have not been checked against.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, NamedTuple

TIMESTAMP_URL = "http://timestamp.acs.microsoft.com"
METADATA_KEYS = ("Endpoint", "CodeSigningAccountName", "CertificateProfileName")
UNSIGNED_NOTE = "unsigned: no Artifact Signing configuration on this machine (docs/code-signing.md)"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

Runner = Callable[[list[str]], tuple[int, str]]


class Config(NamedTuple):
    signtool: Path
    dlib: Path
    metadata: Path


class Result(NamedTuple):
    signed: bool
    files: tuple[Path, ...]
    note: str


def config_path() -> Path:
    named = os.environ.get("SM64_SIGNING_CONFIG")
    if named:
        return Path(named)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "SM64Trainer" / "signing" / "signing.json"


def _sdk_signtool() -> Path | None:
    """The newest Windows SDK's x64 signtool (the dlib must match its architecture)."""
    kits = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits" / "10" / "bin"
    found = sorted(kits.glob("10.*/x64/signtool.exe"), reverse=True, key=lambda path: [
        int(part) for part in path.parent.parent.name.split(".") if part.isdigit()]) if kits.is_dir() else []
    if found:
        return found[0]
    on_path = shutil.which("signtool")
    return Path(on_path) if on_path else None


def load_config(path: Path | None = None) -> Config | None:
    """The machine's signing configuration, or None when there is none.
    A file that exists but is incomplete is an error, not "unsigned"."""
    path = path or config_path()
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as failure:
        raise RuntimeError(f"the signing configuration {path} is not readable JSON ({failure})") from None
    if not isinstance(value, dict) or not value.get("dlib") or not value.get("metadata"):
        raise RuntimeError(f"the signing configuration {path} needs \"dlib\" and \"metadata\" (docs/code-signing.md)")
    signtool = Path(value["signtool"]) if value.get("signtool") else _sdk_signtool()
    config = Config(Path(signtool) if signtool else Path("signtool.exe"), Path(value["dlib"]), Path(value["metadata"]))
    for name, file in (("signtool", config.signtool), ("dlib", config.dlib), ("metadata", config.metadata)):
        if not file.is_file():
            raise RuntimeError(f"the signing configuration names a {name} that does not exist: {file}")
    try:
        metadata = json.loads(config.metadata.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuntimeError(f"the Artifact Signing metadata {config.metadata} is not readable JSON") from None
    missing = [key for key in METADATA_KEYS if not isinstance(metadata, dict) or not metadata.get(key)]
    if missing:
        raise RuntimeError(f"the Artifact Signing metadata {config.metadata} lacks {', '.join(missing)}")
    return config


def sign_command(config: Config, files) -> list[str]:
    return [str(config.signtool), "sign", "/v", "/fd", "SHA256", "/tr", TIMESTAMP_URL, "/td", "SHA256",
            "/dlib", str(config.dlib), "/dmdf", str(config.metadata), *(str(Path(file)) for file in files)]


def verify_command(config: Config, file: Path) -> list[str]:
    return [str(config.signtool), "verify", "/pa", str(file)]


def _run(command: list[str]) -> tuple[int, str]:
    result = subprocess.run(command, capture_output=True, text=True, errors="replace", check=False,
                            creationflags=_NO_WINDOW, stdin=subprocess.DEVNULL, timeout=900)
    return result.returncode, result.stdout + result.stderr


def sign(files, *, config: Config | None = None, config_file: Path | None = None,
         runner: Runner | None = None, log=print) -> Result:
    """Sign `files` in place when this machine is set up to; otherwise say they are unsigned."""
    files = tuple(Path(file) for file in files)
    missing = [file for file in files if not file.is_file()]
    if missing:
        raise RuntimeError("cannot sign files that do not exist: " + ", ".join(map(str, missing)))
    config = config or load_config(config_file)
    if config is None:
        log(f"  code signing: {UNSIGNED_NOTE}")
        return Result(False, files, UNSIGNED_NOTE)
    runner = runner or _run
    code, output = runner(sign_command(config, files))
    if code != 0:
        tail = "\n".join(output.splitlines()[-8:])
        raise RuntimeError(f"signtool could not sign {len(files)} file(s) (exit {code}):\n{tail}")
    for file in files:
        code, output = runner(verify_command(config, file))
        if code != 0:
            raise RuntimeError(f"signtool signed but {file} does not verify (exit {code}): "
                               + " | ".join(output.splitlines()[-3:]))
    log(f"  code signing: signed and verified {', '.join(file.name for file in files)}")
    return Result(True, files, f"signed {len(files)} file(s) with Artifact Signing")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if args == ["--status"] or not args:
            config = load_config()
            print(f"code signing: {'configured' if config else UNSIGNED_NOTE} ({config_path()})")
            return 0
        sign(args)
    except RuntimeError as failure:
        print(f"code signing FAILED: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
