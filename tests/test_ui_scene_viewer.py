"""The Scene viewer's page (M4 E3, lab only): its request logic in jsdom, and its absence.

With the lab switched off the shell must stay exactly as it was: app.js may reach the
viewer only through the dynamic import its probe answers, and nothing the app always
loads may name the viewer's module or stylesheet. Rendered layout and feel are the lead's
visual check (a GPU browser cannot run while Griffin plays).
"""
import re
from pathlib import Path

from frontend_runner import run_frontend
from source_scan import strip_comments

UI = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"


def test_the_viewer_asks_for_one_picture_at_a_time_and_says_why_when_it_cannot():
    run_frontend("sceneviewer.test.js")


def _static_imports(source: str) -> list[str]:
    return re.findall(r"^\s*import\s[^;]*?from\s+[\"']([^\"']+)[\"']", source, re.MULTILINE)


def test_the_shell_reaches_the_viewer_only_after_the_server_says_it_exists():
    app = strip_comments((UI / "app.js").read_text(encoding="utf-8"))
    assert not any("sceneviewer" in name for name in _static_imports(app))
    probe = app.index('fetch("/api/scene-lab"')
    lazy = app.index('import("./components/sceneviewer.js")')
    assert probe < lazy and "response.ok ?" in app[probe:lazy + 40]
    index = (UI / "index.html").read_text(encoding="utf-8")
    assert "scenelab.css" not in index and "sceneviewer" not in index


def test_the_guards_can_still_fail():
    assert _static_imports('import { SceneViewer } from "./components/sceneviewer.js";\n') == [
        "./components/sceneviewer.js"]
    assert _static_imports('const lazy = () => import("./components/sceneviewer.js");\n') == []
