"""Build the independent MIT transport from an explicitly provided private SDK.

No SDK download, account setup, configuration collection or application launch.
Only the helper, verified shipping DLL, notices and a hash receipt reach --out.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "integrations/eos-transport"
DLL_SHA256 = "ab25af350c48856633d671a16e8469277f9acee39186d69fe5b2a26100d738e8"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(sdk: Path, output: Path):
    dll = sdk / "Bin/EOSSDK-Win64-Shipping.dll"
    library = sdk / "Lib/EOSSDK-Win64-Shipping.lib"
    if not dll.is_file() or digest(dll) != DLL_SHA256 or not library.is_file():
        raise ValueError("The pinned EOS C SDK 1.19.2.1 inputs are required.")
    include = sdk / "Include"
    if not (include / "eos_sdk.h").is_file():
        raise ValueError("The EOS C SDK headers are missing.")
    output.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location("eos_toolchain", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    vcvars = compiler.find_vcvars64()
    if vcvars is None:
        raise RuntimeError("MSVC x64 is required.")
    flags = ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", "/utf-8"]
    compiler._cl(vcvars, [*flags, str(SOURCE / "core_test.cpp"),
        f"/Fe:{output / 'eos-core-test.exe'}", f"/Fo{output}\\", "/link", "/MANIFEST:EMBED",
        "/MANIFESTUAC:level='asInvoker'"], output)
    check = subprocess.run([str(output / "eos-core-test.exe")], cwd=output,
        capture_output=True, timeout=20, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if check.returncode:
        raise RuntimeError("EOS transport SDK-free checks failed.")
    compiler._cl(vcvars, [*flags, f"/I{include}", str(SOURCE / "main.cpp"),
        f"/Fe:{output / 'eos-transport.exe'}", f"/Fo{output}\\", "/link", str(library),
        "kernel32.lib", "bcrypt.lib", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'"], output)
    shutil.copyfile(dll, output / dll.name)
    shutil.copyfile(SOURCE / "LICENSE", output / "transport-LICENSE.txt")
    notices = sdk.parent / "ThirdPartyNotices/ThirdPartySoftwareNotice.txt"
    if not notices.is_file():
        raise ValueError("The SDK third-party notices are required.")
    shutil.copyfile(notices, output / "EOS-ThirdPartySoftwareNotice.txt")
    report = {"sdk": "1.19.2.1", "architecture": "x64", "core_checks_passed": True,
        "inputs": {path.name: digest(path) for path in sorted(SOURCE.iterdir()) if path.is_file()},
        "header_inputs_sha256": hashlib.sha256(b"".join(
            path.relative_to(include).as_posix().encode() + b"\0" + path.read_bytes()
            for path in sorted(include.rglob("*.h")))).hexdigest(),
        "library_sha256": digest(library),
        "outputs": {name: {"bytes": (output / name).stat().st_size, "sha256": digest(output / name)}
            for name in ("eos-transport.exe", dll.name)}}
    (output / "build-receipt.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    options = parser.parse_args()
    os.environ.setdefault("VSCMD_SKIP_SENDTELEMETRY", "1")
    report = build(options.sdk.resolve(), options.out.resolve())
    print(json.dumps({"ok": True, "sdk": report["sdk"], "outputs": report["outputs"]}))


if __name__ == "__main__":
    main()
