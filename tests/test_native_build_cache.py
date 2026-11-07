"""The test compile cache keeps every output inside its own entry (tools/native_build_cache.py).

Before recipe 3 an output named by /Fe: outside the working folder had the
role "/Fe", and `entry / "out" / "/Fe"` is the drive root on Windows: the cache
stored such outputs in C:\\Fe and restored them from there, so two builds with
one output name shared one file and a hit restored the wrong binary
(2026-11-06). A fake compiler stands in for cl, so no toolchain is needed.
"""
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import native_build_cache  # noqa: E402

FIRST = "int main(void) { return 0; }\n"
SECOND = "int main(void) { return 1; }\n"


@pytest.fixture
def compiler(monkeypatch):
    """A cl stand-in: it writes the /Fe: target with the given bytes and reports one include."""
    calls = []

    def run(vcvars, args, cwd):
        calls.append(args)
        target = Path(next(arg for arg in args if arg.startswith("/Fe:"))[4:])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(run.payload)
        header = Path(cwd) / "probe.h"
        return native_build_cache.subprocess.CompletedProcess(args, 0, f"Note: including file: {header}\n", "")
    run.payload = b"MZ first build"
    monkeypatch.setattr(native_build_cache, "_run", run)
    return run, calls


def _call(tmp_path, out: Path, source: str = FIRST):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    (work / "probe.c").write_text(source)
    (work / "probe.h").write_text("/* header */\n")
    return [str(work / "probe.c"), f"/Fe:{out}", "/link", "user32.lib"], work


def test_an_output_outside_the_working_folder_is_stored_inside_the_cache(tmp_path, compiler):
    _, calls = compiler
    name = f"probe-{uuid.uuid4().hex[:12]}.exe"  # nobody else's file, wherever it lands
    out = tmp_path / "elsewhere" / name
    args, work = _call(tmp_path, out)
    cache = tmp_path / "cache"
    native_build_cache.cl(Path("vcvars32.bat"), args, work, cache)
    assert not any((Path(out.anchor) / role / name).exists() for role in ("Fe", "Fo", "Fd")), \
        "the cache wrote an output at the drive root"
    stored = list(cache.rglob(name))
    assert len(stored) == 1 and stored[0].resolve().is_relative_to(cache.resolve())
    assert stored[0].read_bytes() == b"MZ first build"

    out.unlink()
    native_build_cache.cl(Path("vcvars32.bat"), args, work, cache)
    assert len(calls) == 1, "the identical second call is a cache hit"
    assert out.read_bytes() == b"MZ first build"


def test_two_outputs_with_one_name_never_share_a_file(tmp_path, compiler):
    run, calls = compiler
    name = f"twin-{uuid.uuid4().hex[:12]}.dll"
    cache = tmp_path / "cache"
    first, work = _call(tmp_path, tmp_path / "a" / name)
    native_build_cache.cl(Path("vcvars32.bat"), first, work, cache)
    run.payload = b"MZ second build"
    second, _ = _call(tmp_path, tmp_path / "b" / name, SECOND)  # other sources, the same output name
    native_build_cache.cl(Path("vcvars32.bat"), second, work, cache)
    (tmp_path / "a" / name).unlink()
    first, _ = _call(tmp_path, tmp_path / "a" / name)
    native_build_cache.cl(Path("vcvars32.bat"), first, work, cache)  # a hit for the first sources
    assert len(calls) == 2
    assert (tmp_path / "a" / name).read_bytes() == b"MZ first build"


def test_a_manifest_role_that_leaves_its_entry_is_never_followed(tmp_path):
    entry = tmp_path / "cache" / "ab" / "entry"
    for role in ("/Fe", "..\\..", "../.."):
        with pytest.raises(ValueError, match="leave its entry"):
            native_build_cache._stored(entry, role, "probe.exe")
    assert native_build_cache._stored(entry, "Fe", "probe.exe") == entry / "out" / "Fe" / "probe.exe"
