"""THE door to the Ghost Lab's own code, the modules in tools/scene_lab.

From a source checkout those modules are plain scripts beside each other and
import each other by bare name (`import package`, `import replay`), so the
folder goes on `sys.path` and a module is imported by that name. The installed
app has no tools folder: tools/build_exe.py collects every module named in
tools/bundle_policy.py's LAB_MODULES into the app under the same bare name, and
here they are imported exactly as they are. LAB_MODULES is the one list of what
the installed app carries; tests/test_scenelab_code.py proves it equals the
import closure of what the app reaches, and that no bare name shadows a
standard library, installed or app module.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

#: tools/scene_lab in this checkout; absent from the installed app
LAB_SOURCE = Path(__file__).resolve().parents[2] / "tools" / "scene_lab"


def _on_path() -> None:
    """From a source checkout, put tools/scene_lab first on `sys.path` once."""
    if getattr(sys, "frozen", False):
        return
    folder = str(LAB_SOURCE)
    if folder not in sys.path:
        sys.path.insert(0, folder)


def lab_module(name: str) -> ModuleType:
    """The Ghost Lab module `name` (a bare name such as "scene_session"):
    imported from tools/scene_lab in a source checkout, from the app itself
    when installed."""
    _on_path()
    return importlib.import_module(name)
