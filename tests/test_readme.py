"""The README's pictures and links keep working as the repo moves.

His ask (2026-10-24): a README that shows off the features "the only thing:
we have to make sure this is maintainable". The screenshots come from
`tools/readme_media.py`, which shoots named uilab Stories; these checks fail
when a picture the page shows has no source, a shot's Story is renamed away,
or a relative link points at a file that no longer exists.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import readme_media  # noqa: E402

PAGES = ("README.md", "CONTRIBUTING.md", "docs/user-guide.md")
LINK = re.compile(r"""(?:\]\(|src=")([^)"#\s]+)""")


def _tracked() -> set[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8", check=True).stdout
    return set(out.splitlines())


def test_every_relative_link_and_picture_resolves():
    tracked = _tracked()
    broken = []
    for page in PAGES:
        base = (ROOT / page).parent
        for target in LINK.findall((ROOT / page).read_text(encoding="utf-8")):
            if re.match(r"[a-z]+:", target):
                continue
            path = (base / target).resolve().relative_to(ROOT).as_posix()
            if path not in tracked and not any(
                    t.startswith(path.rstrip("/") + "/") for t in tracked):
                broken.append(f"{page}: {target}")
    assert not broken, broken


def test_every_shot_is_shown_exists_and_names_a_real_story():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    stories = set(re.findall(r'Story\(name="([^"]+)"',
                             (ROOT / "tools" / "uilab_project.py").read_text(encoding="utf-8")))
    for shot in readme_media.SHOTS:
        assert shot.story in stories, f"{shot.name}: no uilab Story named {shot.story!r}"
        assert (readme_media.OUT / f"{shot.name}.png").is_file(), (
            f"docs/readme/{shot.name}.png is missing: run tools/readme_media.py")
        assert f"docs/readme/{shot.name}.png" in readme, f"README never shows {shot.name}"
    known = {s.name for s in readme_media.SHOTS} | set(readme_media.HUMAN_CAPTURES)
    for shown in re.findall(r"docs/readme/([\w-]+)\.\w+", readme):
        assert shown in known, (
            f"README shows docs/readme/{shown} but readme_media.py neither makes it "
            "nor lists it in HUMAN_CAPTURES")
