"""Tracked source and docs stay LF.

Worker checkouts on Windows committed 54 files with CRLF or mixed endings
during the replay feature (found 2026-09-16): every later edit read as a
whole-file rewrite, and the native build ids, which hash source bytes, changed
with no code change. The four CRLF files main already had are listed so a new
one is caught; convert one and remove it here.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = (".py", ".md", ".js", ".c", ".cpp", ".h", ".html", ".css", ".toml", ".json", ".bat", ".def")
LEGACY_CRLF = {
    "pyproject.toml",
    "src/sm64_events/core/version.py",
    "src/sm64_events/data/rank_standards.seed.json",
    "tests/fixtures/xcams_catalog.json",
}


def tracked_text_files():
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    for name in listed.decode("utf-8").split("\0"):
        if name.endswith(TEXT) and not name.startswith("tests/data/") and "/vendor/" not in name:
            yield name


def test_tracked_text_files_use_lf():
    crlf = [name for name in tracked_text_files()
            if name not in LEGACY_CRLF and (ROOT / name).is_file()
            and b"\r\n" in (ROOT / name).read_bytes()]
    assert crlf == [], "convert to LF with a bytes rewrite: " + ", ".join(crlf)


def test_the_legacy_list_names_only_files_that_still_need_it():
    stale = [name for name in LEGACY_CRLF
             if not (ROOT / name).is_file() or b"\r\n" not in (ROOT / name).read_bytes()]
    assert stale == [], "remove from LEGACY_CRLF: " + ", ".join(stale)
