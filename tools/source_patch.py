"""Apply a tracked patch file to an unpacked source tree, each edit to text that occurs exactly once.

SPDX-License-Identifier: GPL-2.0-or-later (licenses/README.md: the plugin's build tools)

A patch file is JSON: {"about": ..., "edits": [{"file", "why", "before", "after"}]}, `file`
relative to the tree. An edit whose `before` is missing, or occurs more than once, stops the
build instead of patching the wrong place: an upstream update must be looked at, not guessed.
Used by tools/build_renderer_libs.py (renderer/patches/) and tools/build_ffmpeg.py (ffmpeg/patches/).
"""
from __future__ import annotations

import json
from pathlib import Path


def apply_patch(tree: Path, patch: Path) -> None:
    for edit in json.loads(patch.read_text(encoding="utf-8"))["edits"]:
        path = tree / edit["file"]
        data = path.read_bytes()
        before, after = edit["before"].encode("utf-8"), edit["after"].encode("utf-8")
        if data.count(before) != 1:
            raise ValueError(f"{patch.name}: {edit['file']} no longer holds its anchor exactly once "
                             f"({data.count(before)} times)")
        path.write_bytes(data.replace(before, after))
