"""A content-keyed cache for the test suite's cl.exe invocations.

`tools/build_plugin.py::_cl` delegates here when SM64_NATIVE_BUILD_CACHE names
a folder; tests/conftest.py sets it, shipped builds never do. An unchanged host
is then a copy instead of a compile. A miss also costs less than before: the
toolchain environment is captured once per process instead of running
vcvars32.bat (about 1.4 s) around every compile, and several sources compile
in parallel (/MP).

The key is the command line with every input file replaced by its content
hash, the checkout root and the working folder replaced by placeholders (so
sibling worktrees share entries) and the output names kept. A hit also needs
every header the compile read, as `/showIncludes` reported it, to hash the
same. Toolchain headers are covered by the vcvars path in the key instead.
Only successful compiles are stored; a failure always reruns cl.

Limit: a header newly added EARLIER on the include path than one recorded in
the manifest would be missed. No test does that; clear the folder if one does.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

RECIPE = "2"  # 2: /showIncludes reaches the compiler when the call links
REPO = Path(__file__).resolve().parents[1]
MAX_ENTRIES = 400
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_TOOLCHAIN_MARKERS = ("\\microsoft visual studio\\", "\\windows kits\\")
_OUTPUT_OPTIONS = ("/Fe:", "/Fe", "/Fo:", "/Fo", "/Fd:", "/Fd")
_SOURCE_SUFFIXES = {".c", ".cpp", ".cc", ".cxx"}
_environments: dict[str, dict[str, str]] = {}


def enabled() -> Path | None:
    value = os.environ.get("SM64_NATIVE_BUILD_CACHE", "")
    return Path(value) if value and value != "0" else None


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def environment(vcvars: Path) -> dict[str, str]:
    """vcvars32/64's environment, captured once per process."""
    key = str(vcvars)
    if key not in _environments:
        # A batch file, as in build_plugin._cl: cmd sees the quotes as written.
        with tempfile.TemporaryDirectory(prefix="vcvars-") as scratch:
            script = Path(scratch) / "environment.bat"
            # Without the telemetry opt-out vcvars may start vctip.exe, which inherits
            # this pipe and holds the capture open for minutes after vcvars returns.
            script.write_text('@echo off\r\nset VSCMD_SKIP_SENDTELEMETRY=1\r\n'
                              f'call "{vcvars}" >nul\r\nif errorlevel 1 exit /b 1\r\nset\r\n', encoding="utf-8")
            result = _spawn(["cmd.exe", "/d", "/c", str(script)], None, None)
        if result.returncode != 0:
            raise RuntimeError(f"vcvars failed ({result.returncode}):\n{result.stdout}\n{result.stderr}")
        env = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line[1:])
        env["VSLANG"] = "1033"  # /showIncludes prefix in English
        _environments[key] = env
    return _environments[key]


def _output_dirs(args: list[str], cwd: Path) -> dict[str, Path]:
    dirs = {"cwd": cwd}
    for arg in args:
        for option in _OUTPUT_OPTIONS:
            if arg.startswith(option) and len(arg) > len(option):
                value = arg[len(option):]
                target = Path(value) if Path(value).is_absolute() else cwd / value
                role = option[:3]
                dirs[role] = target if value.endswith(("\\", "/")) else target.parent
                break
    return dirs


def _normalize(text: str, cwd: Path) -> str:
    for path, name in sorted(((str(cwd), "<cwd>"), (str(REPO), "<root>")), key=lambda item: -len(item[0])):
        text = re.sub(re.escape(path), name, text, flags=re.IGNORECASE)
    return text.replace("/", "\\")


def _input(arg: str, cwd: Path) -> Path | None:
    """A source, object or library named on the command line."""
    if arg.startswith("/"):
        return None
    path = Path(arg) if Path(arg).is_absolute() else cwd / arg
    return path if path.is_file() else None


def _key(vcvars: Path, args: list[str], cwd: Path) -> str:
    parts = [RECIPE, str(vcvars).lower()]
    for arg in args:
        candidate = _input(arg, cwd)
        if candidate is not None:
            parts.append(f"file:{candidate.name}:{_sha(candidate)}")
        elif arg.startswith(_OUTPUT_OPTIONS):
            option = next(option for option in _OUTPUT_OPTIONS if arg.startswith(option))
            value = arg[len(option):]
            parts.append(option + ("<dir>" if value.endswith(("\\", "/")) else Path(value).name))
        else:
            parts.append(_normalize(arg, cwd))
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def _resolve(recorded: str, cwd: Path) -> Path:
    return Path(recorded.replace("<root>", str(REPO)).replace("<cwd>", str(cwd)))


def _snapshot(dirs: dict[str, Path]) -> dict[Path, tuple[int, int]]:
    seen = {}
    for folder in set(dirs.values()):
        if folder.is_dir():
            for path in folder.iterdir():
                if path.is_file():
                    stat = path.stat()
                    seen[path] = (stat.st_mtime_ns, stat.st_size)
    return seen


def _restore(entry: Path, manifest: dict, dirs: dict[str, Path]) -> bool:
    for role, _name in manifest["outputs"]:
        if role not in dirs:
            return False
    for role, name in manifest["outputs"]:
        dirs[role].mkdir(parents=True, exist_ok=True)
        shutil.copyfile(entry / "out" / role / name, dirs[role] / name)
    os.utime(entry / "manifest.json")
    return True


def _valid(manifest: dict, cwd: Path) -> bool:
    for recorded, digest in manifest["includes"]:
        path = _resolve(recorded, cwd)
        if not path.is_file() or _sha(path) != digest:
            return False
    return True


def _run(vcvars: Path, args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = environment(vcvars)
    compiler = shutil.which("cl", path=env.get("PATH") or env.get("Path"))
    if compiler is None:
        raise RuntimeError(f"cl.exe not on the PATH {vcvars} sets")
    return _spawn([compiler, *args], cwd, env)


def _spawn(command: list[str], cwd: Path | None, env: dict | None) -> subprocess.CompletedProcess:
    """Run to exit with output in files, not pipes. The toolchain may start
    vctip.exe (telemetry), which inherits a pipe and keeps it open for about
    two minutes after cl returns: a pipe reader waits for it, a file does not."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        result = subprocess.run(command, cwd=None if cwd is None else str(cwd), env=env, stdout=out,
                                stderr=err, creationflags=_NO_WINDOW, check=False, stdin=subprocess.DEVNULL)
        out.seek(0)
        err.seek(0)
        return subprocess.CompletedProcess(command, result.returncode,
                                           out.read().decode("utf-8", "replace"),
                                           err.read().decode("utf-8", "replace"))


def _compiler_args(args: list[str]) -> list[str]:
    """The call's own arguments plus /showIncludes, and /MP for several
    sources, both BEFORE /link: after it they are linker options, ignored."""
    link = args.index("/link") if "/link" in args else len(args)
    extra = ["/showIncludes"]
    sources = sum(1 for arg in args[:link] if not arg.startswith("/") and Path(arg).suffix.lower() in _SOURCE_SUFFIXES)
    single_object = any(arg.startswith(("/Fo", "/Fo:")) and not arg.endswith(("\\", "/")) for arg in args)
    if sources >= 2 and not single_object and not any(arg.startswith("/MP") for arg in args):
        extra.append(f"/MP{min(8, sources)}")
    return [*args[:link], *extra, *args[link:]]


def _prune(root: Path) -> None:
    entries = sorted((path for path in root.glob("*/*") if (path / "manifest.json").is_file()),
                     key=lambda path: (path / "manifest.json").stat().st_mtime)
    for stale in entries[:max(0, len(entries) - MAX_ENTRIES)]:
        shutil.rmtree(stale, ignore_errors=True)


def cl(vcvars: Path, args: list, cwd: Path, root: Path) -> None:
    """Compile like build_plugin._cl, or copy the identical earlier result."""
    args = [str(arg) for arg in args]
    cwd = Path(cwd)
    key = _key(vcvars, args, cwd)
    dirs = _output_dirs(args, cwd)
    entry = root / key[:2] / key
    try:
        manifest = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))
        if _valid(manifest, cwd) and _restore(entry, manifest, dirs):
            return
    except (OSError, ValueError, KeyError):
        pass
    before = _snapshot(dirs)
    result = _run(vcvars, _compiler_args(args), cwd)
    if result.returncode != 0:
        raise RuntimeError(f"cl failed ({result.returncode}):\n{result.stdout}\n{result.stderr}")
    includes, reported = {}, False
    for line in result.stdout.splitlines():
        if line.startswith("Note: including file:"):
            reported = True
            path = Path(line.split(":", 2)[2].strip())
            if not any(marker in str(path).lower() for marker in _TOOLCHAIN_MARKERS):
                includes[_normalize(str(path.resolve()), cwd)] = _sha(path)
    compiled = any(_input(arg, cwd) is not None and Path(arg).suffix.lower() in _SOURCE_SUFFIXES for arg in args)
    if compiled and not reported:
        return  # no include list to prove a later hit by: never store it
    outputs = []
    for path, stamp in _snapshot(dirs).items():
        if before.get(path) != stamp and path.name != "build_step.bat":
            role = next(role for role, folder in dirs.items() if folder == path.parent)
            outputs.append((role, path.name))
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="entry-", dir=root))
    for role, name in outputs:
        (staging / "out" / role).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dirs[role] / name, staging / "out" / role / name)
    (staging / "manifest.json").write_text(json.dumps(
        {"includes": sorted(includes.items()), "outputs": sorted(outputs)}), encoding="utf-8")
    entry.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(entry, ignore_errors=True)
    try:
        os.replace(staging, entry)
    except OSError:  # a parallel worker stored the same entry first
        shutil.rmtree(staging, ignore_errors=True)
    _prune(root)
