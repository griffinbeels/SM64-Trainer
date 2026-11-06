"""The scene tap keeps to its own lane (clean-behavior, 2026-11-06).

Defender quarantined our renderer on a machine-learning verdict; the live recording's old shape read
like spyware and ransomware: a compressor looked up by name at run time, thousands of short-lived
files created and deleted, earlier folders listed and emptied, and a recording that started when a
file was renamed into place. The shipped wrapper's scene tap (plugin/gfxwrap/scene_tap.cpp, its
writer scene_spool.cpp and their headers) now does none of that: the compressor is the Compression
API linked by import, the spool is a fixed set of files reused in place, and recording starts only
from the Trainer's lease. The one-shot mode (armed.txt, renamed into a session folder) is compiled
only into the offline host (SCENE_TAP_ONE_SHOT). This guard reads the shipped source as the
compiler sees it without that define, comments removed, and names each Windows call that would
bring a pattern back. The behaviour itself (files reused, none deleted, counts in summary.txt) is
tests/test_scene_live_spool.py's.
"""
import re
from pathlib import Path

from source_scan import strip_comments

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = [ROOT / "plugin/gfxwrap" / name for name in ("scene_tap.cpp", "scene_tap.h", "scene_spool.cpp",
                                                       "scene_spool.h", "scene_closure.h")]
FORBIDDEN = {
    "GetProcAddress": "looks a function up by name at run time",
    "GetModuleHandle": "finds a loaded module to look a function up in",
    "LoadLibrary": "loads a module at run time",
    "DeleteFile": "deletes a file",
    "RemoveDirectory": "deletes a folder",
    "FindFirstFile": "lists a folder",
    "MoveFile": "renames a file into place",
    "CREATE_ALWAYS": "makes a file anew on every open",
}
ONE_SHOT = "SCENE_TAP_ONE_SHOT"


def shipped(source: str) -> str:
    """The lines a build without SCENE_TAP_ONE_SHOT compiles (only that macro's #ifdef, #ifndef,
    #else and #endif are followed; every other conditional is kept whole)."""
    kept, stack = [], []          # per open conditional: "one" (one-shot only), "not" (shipped only), "other"
    for line in source.splitlines():
        token = " ".join(line.split())
        if token.startswith("#if"):
            stack.append("one" if token == f"#ifdef {ONE_SHOT}" else "not" if token == f"#ifndef {ONE_SHOT}"
                         else "other")
            if stack[-1] != "other":
                continue
        elif token.startswith("#else") and stack and stack[-1] != "other":
            stack[-1] = "not" if stack[-1] == "one" else "one"
            continue
        elif token.startswith("#endif") and stack:
            if stack.pop() != "other":
                continue
        if "one" not in stack:
            kept.append(line)
    return "\n".join(kept)


def violations(sources: dict[str, str]) -> list[str]:
    found = []
    for name, source in sources.items():
        code = shipped(re.sub(r"//[^\n]*", "", strip_comments(source)))   # C++ also ends lines with comments
        found += [f"{name}: {call} ({why})" for call, why in FORBIDDEN.items() if call in code]
    return found


def test_the_shipped_scene_tap_looks_up_deletes_lists_and_renames_nothing():
    assert violations({path.name: path.read_text(encoding="utf-8") for path in SHIPPED}) == []


def test_the_compressor_is_linked_by_import():
    writer = strip_comments((ROOT / "plugin/gfxwrap/scene_spool.cpp").read_text(encoding="utf-8"))
    assert '#pragma comment(lib, "cabinet.lib")' in writer and "CreateCompressor(COMPRESS_ALGORITHM_XPRESS_HUFF" in writer


def test_the_guard_can_still_fail():
    """Each way back in is seen; the one-shot branch and comments are not."""
    assert violations({"a.cpp": "void f() { DeleteFileW(path); }"}) == ["a.cpp: DeleteFile (deletes a file)"]
    assert violations({"b.cpp": "auto f = GetProcAddress(GetModuleHandleW(L\"ntdll.dll\"), \"RtlCompressBuffer\");"}) == [
        "b.cpp: GetProcAddress (looks a function up by name at run time)",
        "b.cpp: GetModuleHandle (finds a loaded module to look a function up in)"]
    one_shot = f"#ifdef {ONE_SHOT}\nMoveFileW(a, b);\n#endif\nint x;"
    assert violations({"c.cpp": one_shot}) == []
    split = f"#ifndef {ONE_SHOT}\nstart_live();\n#else\nMoveFileW(a, b);\n#endif\n#ifndef {ONE_SHOT}\nMoveFileW(a, b);\n#endif"
    assert violations({"d.cpp": split}) == ["d.cpp: MoveFile (renames a file into place)"]
    nested = f"#ifdef {ONE_SHOT}\n#if 1\nint y;\n#endif\nRemoveDirectoryW(p);\n#endif\n#if 0\nFindFirstFileW(p, &f);\n#endif"
    assert violations({"e.cpp": nested}) == ["e.cpp: FindFirstFile (lists a folder)"]
    assert violations({"f.cpp": "/* DeleteFileW was format 1's */ int z; // CREATE_ALWAYS too"}) == []
    real = (ROOT / "plugin/gfxwrap/scene_tap.cpp").read_text(encoding="utf-8")
    assert "MoveFileW" in real and violations({"scene_tap.cpp": real.replace(f"#ifdef {ONE_SHOT}", "#if 1")}) != []
