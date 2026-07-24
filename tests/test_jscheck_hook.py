"""Regression tests for .claude/hooks/jscheck.py — the JS-syntax PostToolUse guard.

WHY THIS FILE EXISTS: the hook shipped blind. `node --check <file>.js` silently
exits 0 for any file containing `import`/`export` (ESM makes node skip the
check unless the file is .mjs), and every UI file is ESM — so for its entire
life the guard passed corrupted files (found 2026-07-24 by corrupting a real
component). The fix feeds the file to node on stdin with --input-type=module.
These tests pin all four quadrants so a future node upgrade or hook edit can't
silently regress the guard back to blindness.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "jscheck.py"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH"
)


def run_hook(file_path: Path) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_input": {"file_path": str(file_path)}})
    return subprocess.run(
        [sys.executable, str(HOOK)], input=payload,
        capture_output=True, text=True, timeout=30,
    )


def write_js(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_broken_esm_is_caught(tmp_path):
    # The exact historical blind spot: ESM syntax + a syntax error.
    p = write_js(tmp_path, "broken_esm.js",
                 "export function ok() { return 1; }\nconst broken = (;\n")
    r = run_hook(p)
    assert r.returncode == 2, f"guard missed broken ESM (stderr: {r.stderr!r})"
    assert str(p) in r.stderr  # hook names the real path, not [stdin]


def test_valid_esm_passes(tmp_path):
    p = write_js(tmp_path, "good_esm.js",
                 "import { h } from './vendor/preact.js';\n"
                 "export const x = () => h('div', null, 'hi');\n")
    r = run_hook(p)
    assert r.returncode == 0, r.stderr


def test_broken_plain_js_is_caught(tmp_path):
    p = write_js(tmp_path, "broken_plain.js", "function f( { return 1; }\n")
    r = run_hook(p)
    assert r.returncode == 2


def test_non_js_ignored(tmp_path):
    p = tmp_path / "notes.md"
    p.write_text("const broken = (;", encoding="utf-8")
    r = run_hook(p)
    assert r.returncode == 0


def test_missing_file_fails_open(tmp_path):
    r = run_hook(tmp_path / "does_not_exist.js")
    assert r.returncode == 0
